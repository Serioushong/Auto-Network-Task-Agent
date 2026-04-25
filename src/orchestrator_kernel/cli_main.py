"""T045 — Top-level kernel assembly + Typer CLI root (US1 MVP).

This module exposes two surfaces:

1. ``assemble_kernel()`` — async factory returning a ``KernelHarness``. Used by
   integration tests (``tests/integration/conftest.py``) and by the CLI's
   ``submit`` command. Wires together:

        config -> audit writer -> payload guard -> (idempotency stub) ->
        LLM planner (rule-based stub for MVP) -> task tree -> dispatcher ->
        supervisor + worker channels -> notifier.

2. ``app`` — the Typer application registered as the ``orchestrator-kernel``
   console script. US1 exposes ``submit`` and ``status`` subcommands; later
   stories append ``approve`` / ``cancel`` (T059 / T067) by reusing
   ``assemble_kernel``.

MVP scope notes (intentional simplifications, flagged with TODO tags):

- ``_plan_capability`` is a rule-based stub: if the user's text starts with
  ``echo``, we plan a single ``echo.say`` leaf; otherwise we plan a single
  ``desktop.click`` leaf. The planner is exactly good enough to satisfy
  ``tests/integration/test_p1_*``. The real planner lands with the LLM
  client integration (T099).
- Idempotency is a no-op stub: every submit creates a fresh traceId. The
  real cache lands in T051 and will wrap ``KernelHarness.submit`` without
  changing the signature.
- All per-task worker I/O runs under an ``anyio.Lock`` scoped to the
  worker channel so parallel submits serialise against the same stdio
  pipe. US1 tests never hit contention; later US5 (parallel traces) will
  upgrade this to a proper request/response multiplexer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import typer
import ulid

from .audit.hasher import hash_canonical_json
from .audit.scanner import scan_and_autofail
from .audit.writer import AuditWriter
from .contracts.approval import ApprovalResponse
from .contracts.audit import AuditEvent, AuditEventType, AuditOutcome
from .contracts.entry_event import EntryEvent, SourceChannel
from .contracts.worker_protocol import (
    AbortFrame,
    DispatchFrame,
    HeartbeatFrame,
    RegisterFrame,
    ResultFrame,
    ShutdownFrame,
    StartedFrame,
)
from .kernel.approval_gate import (
    DEFAULT_APPROVAL_WINDOW_MS,
    ApprovalDecision,
    ApprovalGate,
    ApprovalStaleError,
)
from .kernel.cancel import CancelManager, CancelOutcome, CancelStatus
from .kernel.dispatcher import Dispatcher, NoCapableWorkerError, WorkerHandle
from .kernel.idempotency import CachedTrace, IdempotencyCache
from .kernel.rate_limit import RateLimiter, RateLimits
from .kernel.state_machine import transition
from .kernel.task_tree import LeafPlan, TaskTree, build_from_event
from .kernel.validators import (
    DEFAULT_PAYLOAD_MAX_BYTES,
    PayloadTooLarge,
    _is_actor_safe,
    assert_payload_size,
    build_too_large_audit,
)
from .notifier import delivery as _delivery
from .notifier.delivery import DeliveryChannel, DeliveryFailedError
from .notifier.result_summary import (
    build_kernel_restart_summary,
    build_result_summary,
    print_to_cli,
)
from .worker_supervisor.lifecycle import (
    EscalationStage,
    HeartbeatTracker,
    Violation,
    soft_abort_with_escalation,
)
from .worker_supervisor.protocol import (
    ProtocolFrameError,
    decode_frame,
    encode_frame,
    write_frame,
)
from .worker_supervisor.supervisor import SupervisedWorker, WorkerSupervisor


@dataclass(frozen=True)
class _FakeEscalation:
    """Stand-in ``TerminationResult`` used when no live worker exists."""

    stage: EscalationStage


class _CancelledDuringApproval(Exception):
    """Raised by ``_await_approval`` when a cancel arrived first.

    The caller (``_execute_leaf``) catches this and walks the leaf to
    ``cancelled`` with ``failureReason="user_cancel_before_approval"``
    so spec.md P4 Scenario 2 is honoured without piggybacking on the
    approval ``ApprovalDecision`` enum.
    """

    def __init__(self, task: Any) -> None:
        super().__init__("cancel arrived during approval window")
        self.task = task

# --- CLI help strings live as module-level constants so `ruff E501` stays calm.
_APP_HELP = (
    "Orchestrator Kernel MVP — submit natural-language commands into the "
    "kernel and receive structured ResultSummary responses (US1)."
)


app = typer.Typer(
    name="orchestrator-kernel",
    help=_APP_HELP,
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helper types shared with the integration harness.
# ---------------------------------------------------------------------------


@dataclass
class _FrameEnvelope:
    """One item pulled off the per-worker stdout reader queue.

    Either ``frame`` is populated (non-heartbeat frames routed into the
    dispatcher's await loop) or ``error`` is — representing EOF / decode
    failure / stdout pipe loss. The reader task produces these; only
    ``_dispatch_and_await_result`` consumes them.
    """

    frame: Any | None = None
    error: Exception | None = None


@dataclass
class _WorkerChannel:
    """Couples a ``SupervisedWorker`` with its registration + stdio lock.

    The lock serialises dispatch round-trips per worker — US1 MVP only
    needs one in-flight task per worker, and a lock is the smallest
    primitive that keeps "write dispatch, read started, read result"
    atomic against future concurrent callers.

    ``frame_queue`` / ``reader_task`` implement the T076 heartbeat-aware
    reader refactor: a background task drains stdout continuously, routes
    ``HeartbeatFrame``s to the ``HeartbeatTracker``, and queues every
    other frame for the next dispatch-await to consume. This removes the
    old "only read stdout during a dispatch" limitation that kept
    heartbeats out of reach between tasks.
    """

    worker: SupervisedWorker
    handle: WorkerHandle
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    frame_queue: asyncio.Queue[_FrameEnvelope] = field(
        default_factory=asyncio.Queue
    )
    reader_task: asyncio.Task[None] | None = None
    monitor_task: asyncio.Task[None] | None = None


@dataclass
class TraceResult:
    """Test-facing DTO mirrored from ``tests/integration/_harness.TraceResult``."""

    traceId: str
    eventId: str
    traceOutcome: str
    leafOutcomes: list[str] = field(default_factory=list)
    audit_event_types: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    message: str = ""


# ---------------------------------------------------------------------------
# Rule-based planner stub (replaced by real LLM client in T099).
# ---------------------------------------------------------------------------


def _plan_capability(text: str) -> LeafPlan:
    """Map free-text commands to a single-leaf ``LeafPlan``.

    Rules (MVP):
      - ``"echo <rest>"`` or plain ``"echo"`` -> ``echo.say`` with
        ``payload={"text": <rest or full text>}``;
      - anything else -> ``desktop.click`` with the full text as payload.

    The second branch exists purely so ``test_p1_no_worker`` can exercise
    the ``no_capable_worker`` path without also depending on the planner
    returning an error.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("echo"):
        remainder = stripped[4:].strip() or stripped
        return LeafPlan(
            capability="echo.say",
            payload={"text": remainder},
            risk_level="NORMAL",
        )
    if lowered.startswith("sleep"):
        remainder = stripped[5:].strip()
        try:
            seconds = float(remainder) if remainder else 1.0
        except ValueError:
            seconds = 1.0
        return LeafPlan(
            capability="sleep.wait",
            payload={"seconds": seconds, "text": stripped},
            risk_level="NORMAL",
        )
    if lowered.startswith("crash"):
        remainder = stripped[5:].strip().lower()
        if remainder.startswith("oom"):
            cap = "crash.oom"
        else:
            cap = "crash.raise"
        return LeafPlan(
            capability=cap,
            payload={"text": stripped},
            risk_level="NORMAL",
        )
    if lowered.startswith("burn"):
        return LeafPlan(
            capability="budget.burn",
            payload={"text": stripped},
            risk_level="NORMAL",
        )
    if lowered.startswith("noop"):
        return LeafPlan(
            capability="silent.noop",
            payload={"text": stripped},
            risk_level="NORMAL",
        )
    if lowered.startswith("blast"):
        return LeafPlan(
            capability="oom.blast",
            payload={"text": stripped},
            risk_level="NORMAL",
        )
    if lowered.startswith(("delete", "rm ", "remove")):
        for prefix in ("delete", "remove", "rm"):
            if lowered.startswith(prefix):
                path = stripped[len(prefix):].strip() or stripped
                break
        else:  # pragma: no cover - defensive
            path = stripped
        return LeafPlan(
            capability="file.delete",
            payload={"path": path, "text": stripped},
            risk_level="HIGH_RISK",
        )
    return LeafPlan(
        capability="desktop.click",
        payload={"text": stripped},
        risk_level="NORMAL",
    )


# ---------------------------------------------------------------------------
# Worker channel primitives: read register frame, dispatch-and-await.
# ---------------------------------------------------------------------------


async def _read_one_frame_bytes(
    worker: SupervisedWorker, *, timeout_s: float
) -> bytes:
    """Read exactly one LF-terminated line from the worker's stdout."""
    if worker.process.stdout is None:
        raise RuntimeError(
            f"worker {worker.worker_id!r} has no stdout pipe attached"
        )
    try:
        line = await asyncio.wait_for(
            worker.process.stdout.readline(), timeout=timeout_s
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"worker {worker.worker_id!r} produced no frame within {timeout_s}s"
        ) from exc
    if not line:
        raise RuntimeError(
            f"worker {worker.worker_id!r} closed stdout before emitting a frame"
        )
    return line


async def _read_register_frame(
    worker: SupervisedWorker, *, timeout_s: float = 3.0
) -> RegisterFrame:
    raw = await _read_one_frame_bytes(worker, timeout_s=timeout_s)
    frame = decode_frame(raw)
    if not isinstance(frame, RegisterFrame):
        raise ProtocolFrameError(
            f"expected first frame kind=register; got kind={frame.kind!r}",
            raw=raw,
        )
    return frame


async def _worker_reader_loop(
    channel: _WorkerChannel,
    *,
    on_heartbeat: Callable[[str], None],
) -> None:
    """Drain the worker's stdout forever, splitting heartbeats from traffic.

    Each line is parsed with ``decode_frame`` and routed:

    * ``HeartbeatFrame`` -> ``on_heartbeat(worker_id)`` (sync callback
      because it's just a tracker bump). Heartbeats never reach the
      dispatcher's await queue.
    * Everything else (``started`` / ``result`` / any future frame) is
      queued as a ``_FrameEnvelope(frame=…)`` for
      ``_dispatch_and_await_result``.
    * Decode / OS errors are queued as ``_FrameEnvelope(error=…)`` so the
      awaiter surfaces a meaningful exception instead of hanging.
    * EOF closes the loop and queues a sentinel error so any in-flight
      dispatch fails fast instead of timing out.
    """
    worker = channel.worker
    stdout = worker.process.stdout
    if stdout is None:
        await channel.frame_queue.put(
            _FrameEnvelope(
                error=RuntimeError(
                    f"worker {worker.worker_id!r} has no stdout pipe"
                )
            )
        )
        return
    try:
        while True:
            try:
                line = await stdout.readline()
            except (ConnectionResetError, OSError) as exc:
                await channel.frame_queue.put(_FrameEnvelope(error=exc))
                return
            if not line:
                await channel.frame_queue.put(
                    _FrameEnvelope(
                        error=RuntimeError(
                            f"worker {worker.worker_id!r} closed stdout"
                        )
                    )
                )
                return
            try:
                frame = decode_frame(line)
            except ProtocolFrameError as exc:
                await channel.frame_queue.put(_FrameEnvelope(error=exc))
                continue
            if isinstance(frame, HeartbeatFrame):
                try:
                    on_heartbeat(frame.workerId)
                except Exception:  # noqa: BLE001 — tracker must never kill reader
                    pass
                continue
            await channel.frame_queue.put(_FrameEnvelope(frame=frame))
    except asyncio.CancelledError:
        raise


async def _dispatch_and_await_result(
    channel: _WorkerChannel,
    *,
    dispatch: DispatchFrame,
    timeout_s: float,
) -> tuple[StartedFrame, ResultFrame]:
    """Serialise ``dispatch -> started -> result`` under the channel lock.

    Pulls frames from ``channel.frame_queue`` (fed by
    ``_worker_reader_loop``) instead of reading stdout directly, so
    heartbeats emitted between tasks are absorbed out-of-band without
    blocking the worker's pipe buffer.

    Raises:
        TimeoutError: if the total elapsed time exceeds ``timeout_s``.
        ProtocolFrameError: if an intermediate frame fails validation.
        RuntimeError: if the worker closes stdout before emitting ``result``.
    """
    worker = channel.worker
    if worker.process.stdin is None:
        raise RuntimeError(
            f"worker {worker.worker_id!r} has no stdin pipe attached"
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    async with channel.lock:
        # Defensive: discard any residue from a prior dispatch. Under
        # normal operation the queue is empty at this point because the
        # previous dispatch waited for its own ``result`` before releasing
        # the lock, but malformed stubs / aborted traces could leak a
        # frame behind.
        while not channel.frame_queue.empty():
            channel.frame_queue.get_nowait()

        worker.process.stdin.write(encode_frame(dispatch))
        await worker.process.stdin.drain()

        started: StartedFrame | None = None
        result: ResultFrame | None = None
        while result is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"worker {worker.worker_id!r} did not finish dispatch "
                    f"within {timeout_s}s"
                )
            try:
                envelope = await asyncio.wait_for(
                    channel.frame_queue.get(), timeout=remaining
                )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"worker {worker.worker_id!r} did not finish dispatch "
                    f"within {timeout_s}s"
                ) from exc
            if envelope.error is not None:
                raise envelope.error
            frame = envelope.frame
            if isinstance(frame, StartedFrame):
                started = frame
            elif isinstance(frame, ResultFrame):
                result = frame

    if started is None:
        raise ProtocolFrameError(
            "worker emitted result without a preceding started frame"
        )
    return started, result


# ---------------------------------------------------------------------------
# Audit event helpers.
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Fresh ULID-26 for audit / task / trace identifiers (16..64 chars)."""
    return str(ulid.new().str)


def _user_actor(user_id: str) -> str:
    """Collapse a raw user id into the ``user:<id>`` actor form or fall back."""
    if _is_actor_safe(user_id):
        return f"user:{user_id}"
    return "system"


def _build_audit(
    *,
    event_type: AuditEventType,
    actor: str,
    trace_id: str | None = None,
    task_id: str | None = None,
    parent_task_id: str | None = None,
    capability: str | None = None,
    outcome: AuditOutcome | None = None,
    input_hash: str | None = None,
    output_hash: str | None = None,
    extra: dict[str, Any] | None = None,
    idempotent_replay: bool | None = None,
) -> AuditEvent:
    return AuditEvent.model_validate(
        {
            "auditId": uuid4().hex,
            "timestamp": datetime.now(tz=UTC),
            "traceId": trace_id,
            "taskId": task_id,
            "parentTaskId": parent_task_id,
            "actor": actor,
            "capability": capability,
            "eventType": event_type,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "outcome": outcome,
            "idempotent_replay": idempotent_replay,
            "extra": extra,
        }
    )


# ---------------------------------------------------------------------------
# KernelHarness — the object integration tests drive.
# ---------------------------------------------------------------------------


class _CliPrintChannel:
    """Default DeliveryChannel — writes a JSON line per ResultSummary to stdout.

    Wraps :func:`notifier.result_summary.print_to_cli` in the async
    DeliveryChannel contract (T084) so the kernel's normal-trace path and
    the crash-recovery path can share a single delivery code path.
    """

    async def send(self, summary: Any) -> None:
        print_to_cli(summary)


class KernelHarness:
    """Assembled kernel surface consumed by the CLI and by integration tests.

    Owns the lifetime of: audit writer, dispatcher, supervisor, and the set
    of ``_WorkerChannel``s. The kernel loop itself is currently a single
    async method (``submit``) — Phase 2 already gave us all the building
    blocks, so MVP can go directly from EntryEvent to ResultSummary without
    a dedicated event-loop thread.
    """

    def __init__(
        self,
        *,
        audit_writer: AuditWriter,
        dispatcher: Dispatcher,
        supervisor: WorkerSupervisor,
        payload_limit_bytes: int = DEFAULT_PAYLOAD_MAX_BYTES,
        idempotency_cache: IdempotencyCache | None = None,
        approval_gate: ApprovalGate | None = None,
        approval_timeout_ms: int = DEFAULT_APPROVAL_WINDOW_MS,
        cancel_manager: CancelManager | None = None,
        cancel_soft_timeout_s: float = 3.0,
        cancel_hard_timeout_s: float = 1.0,
        heartbeat_interval_s: float = 0.5,
        heartbeat_miss_threshold: int = 3,
        resource_monitor_factory: Callable[
            [WorkerHandle, Any, Callable[[Violation], Awaitable[None]]],
            asyncio.Task[None],
        ]
        | None = None,
        audit_dir: Path | None = None,
        default_channel: DeliveryChannel | None = None,
        warm_start: bool = True,
        rate_limits: RateLimits | None = None,
        rate_limiter_clock: Callable[..., datetime] | None = None,
    ) -> None:
        self._audit = audit_writer
        self._dispatcher = dispatcher
        self._supervisor = supervisor
        self._audit_dir: Path | None = audit_dir or getattr(audit_writer, "_dir", None)
        self._default_channel: DeliveryChannel = (
            default_channel if default_channel is not None else _CliPrintChannel()
        )
        self._ready: bool = bool(warm_start)
        self._payload_limit = payload_limit_bytes
        self._channels: dict[str, _WorkerChannel] = {}
        self._idempotency = idempotency_cache or IdempotencyCache()
        self._approval_gate = approval_gate or ApprovalGate(
            default_window_ms=approval_timeout_ms
        )
        self._approval_timeout_ms = approval_timeout_ms
        self._approval_events: dict[str, asyncio.Event] = {}
        self._approval_lock = asyncio.Lock()
        self._cancel_manager = cancel_manager or CancelManager()
        self._cancel_soft_timeout_s = cancel_soft_timeout_s
        self._cancel_hard_timeout_s = cancel_hard_timeout_s
        self._heartbeat_tracker = HeartbeatTracker(
            interval_s=heartbeat_interval_s,
            miss_threshold=heartbeat_miss_threshold,
            on_unhealthy=self._on_worker_unhealthy,
            on_recovered=self._on_worker_recovered,
        )
        self._heartbeat_runner: asyncio.Task[None] | None = None
        self._resource_monitor_factory = resource_monitor_factory
        self._sandboxed_workers: set[str] = set()
        # T092 / T093 — 4-dim rate limiter (FR-026 / FR-027). Lock serialises
        # try_admit / release so concurrent submits never race the counters.
        self._rate_limiter = RateLimiter(
            rate_limits or RateLimits(),
            clock=self._normalize_clock(rate_limiter_clock),
        )
        self._rate_lock = asyncio.Lock()

    # --- lifecycle ---------------------------------------------------------

    def _ensure_heartbeat_runner(self) -> None:
        """Spin up the single ``HeartbeatTracker.run()`` task on first call.

        Creating it lazily keeps ``assemble_kernel`` cheap (no running loop
        needed at construction time) and lets tests that never register
        a worker avoid a dangling task.
        """
        if self._heartbeat_runner is None or self._heartbeat_runner.done():
            self._heartbeat_runner = asyncio.create_task(
                self._heartbeat_tracker.run(),
                name="heartbeat-tracker",
            )

    def _on_worker_unhealthy(self, worker_id: str) -> None:
        """HeartbeatTracker callback → flip dispatcher + emit audit."""
        try:
            self._dispatcher.set_health(worker_id, healthy=False)
        except Exception:  # noqa: BLE001 — worker may already be gone
            pass
        self._audit.write(
            _build_audit(
                event_type="worker_unhealthy",
                actor="kernel",
                extra={
                    "workerId": worker_id,
                    "reason": "missed_heartbeats",
                    "missThreshold": self._heartbeat_tracker.miss_threshold,
                },
            )
        )

    def _on_worker_recovered(self, worker_id: str) -> None:
        """HeartbeatTracker callback → re-enable dispatcher + emit audit."""
        try:
            self._dispatcher.set_health(worker_id, healthy=True)
        except Exception:  # noqa: BLE001
            pass
        self._audit.write(
            _build_audit(
                event_type="worker_recovered",
                actor="kernel",
                extra={"workerId": worker_id},
            )
        )

    async def _on_sandbox_violation(
        self, worker_id: str, violation: Violation
    ) -> None:
        """ResourceMonitor callback → audit, flag channel, tear worker down.

        The ``_sandboxed_workers`` set is populated *before* the teardown so
        that any leaf currently mid-dispatch on this worker — whose stdout
        is about to EOF when the kill lands — is mapped to
        ``failureReason="sandbox_limit"`` (FR-025) rather than
        ``worker_crashed``. The flag is cleared in ``_execute_leaf``'s
        error branch once the mapping has been applied.
        """
        self._sandboxed_workers.add(worker_id)
        self._audit.write(
            _build_audit(
                event_type="sandbox_limit_hit",
                actor="kernel",
                extra={
                    "workerId": worker_id,
                    "dimension": violation.dimension,
                    "actual": violation.actual,
                    "limit": violation.limit,
                },
            )
        )
        # Wake any in-flight ``_dispatch_and_await_result`` BEFORE we tear
        # the channel down: pushing an error envelope onto the queue lets
        # ``_execute_leaf`` observe the failure immediately (and map it to
        # ``sandbox_limit`` via ``_sandboxed_workers``) instead of waiting
        # for the wall-clock timeout to fire.
        channel = self._channels.get(worker_id)
        if channel is not None:
            with contextlib.suppress(Exception):
                channel.frame_queue.put_nowait(
                    _FrameEnvelope(
                        error=RuntimeError(
                            f"sandbox_limit_hit "
                            f"dim={violation.dimension} "
                            f"actual={violation.actual} limit={violation.limit}"
                        )
                    )
                )
        self._tear_down_tainted_worker(worker_id)

    async def register_worker(self, spec: Any) -> None:
        """Spawn the worker subprocess and register its capabilities.

        Accepts any object with ``script_path`` + ``expected_capabilities``
        attributes (the integration-harness ``WorkerSpec`` dataclass
        satisfies this). ``expected_capabilities`` is checked only as a
        debug assertion so malformed stubs fail loudly.
        """
        script_path = Path(spec.script_path)
        worker = await self._supervisor.spawn(script_path)
        register = await _read_register_frame(worker)
        handle = WorkerHandle.from_registration(register.registration)

        # T074 late-bind: the Worker's self-declared ResourceLimits only
        # arrive in the register frame, so we cannot pass them to
        # ``spawn(resource_limits=...)``. ``bind_sandbox`` wraps the
        # running PID in a Windows Job Object (no-op on POSIX). This is
        # what makes kernel-enforced ``memory_mb`` real — without it we
        # only have the psutil ``watch_worker`` soft monitor.
        with contextlib.suppress(Exception):
            self._supervisor.bind_sandbox(
                handle.worker_id, register.registration.resourceLimits
            )

        # Begin liveness tracking BEFORE starting the reader so the first
        # heartbeat (which can arrive ~immediately) lands in a known bucket.
        self._ensure_heartbeat_runner()
        self._heartbeat_tracker.track(handle.worker_id)

        channel = _WorkerChannel(worker=worker, handle=handle)
        channel.reader_task = asyncio.create_task(
            _worker_reader_loop(
                channel,
                on_heartbeat=self._heartbeat_tracker.feed,
            ),
            name=f"worker-reader-{handle.worker_id}",
        )
        self._channels[handle.worker_id] = channel
        self._dispatcher.register(handle)

        if self._resource_monitor_factory is not None:
            worker_id = handle.worker_id

            async def _violation_cb(v: Violation) -> None:
                await self._on_sandbox_violation(worker_id, v)

            try:
                channel.monitor_task = self._resource_monitor_factory(
                    handle,
                    register.registration.resourceLimits,
                    _violation_cb,
                )
            except Exception:  # noqa: BLE001 — monitor is best-effort
                channel.monitor_task = None

        expected = tuple(getattr(spec, "expected_capabilities", ()))
        for cap_name in expected:
            if cap_name not in handle.capability_names:
                raise RuntimeError(
                    f"worker {script_path} did not declare expected capability "
                    f"{cap_name!r}; got {sorted(handle.capability_names)}"
                )

        self._audit.write(
            _build_audit(
                event_type="worker_registered",
                actor=f"worker:{handle.worker_id}",
                extra={
                    "workerId": handle.worker_id,
                    "capabilities": sorted(handle.capability_names),
                },
            )
        )

    def _tear_down_tainted_worker(self, worker_id: str) -> None:
        """Kill + deregister a worker whose channel is no longer trustworthy.

        Invoked on wall-budget violations and on post-dispatch crashes so
        subsequent dispatches never read stale ``started`` / ``result``
        frames from a buffered stdout. Best-effort: every step is guarded
        so a partial kill (e.g. race with supervisor shutdown) still lets
        the trace terminate cleanly.
        """
        self._heartbeat_tracker.untrack(worker_id)
        self._dispatcher.unregister(worker_id)
        channel = self._channels.pop(worker_id, None)
        worker = self._supervisor._workers.pop(worker_id, None)
        if channel is not None and channel.reader_task is not None:
            if not channel.reader_task.done():
                channel.reader_task.cancel()
        if channel is not None and channel.monitor_task is not None:
            if not channel.monitor_task.done():
                channel.monitor_task.cancel()
        candidates = []
        if channel is not None:
            candidates.append(channel.worker)
        if worker is not None:
            candidates.append(worker)
        for sup in candidates:
            proc = getattr(sup, "process", None)
            if proc is None or proc.returncode is not None:
                continue
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                continue

    async def startup(
        self,
        *,
        recovery_channel: DeliveryChannel | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Boot sequence: scan audit dir, compensate in-flight Tasks, push summaries.

        Implements FR-028 / FR-029 / SC-009. The kernel MUST refuse new
        intake until this method returns. Idempotent within a single process
        lifetime — calling it after the kernel is already ``_ready`` is a
        no-op (also makes tests robust to retries).

        Args:
            recovery_channel: where to push the per-trace
                ``kernel_restarted`` ResultSummary. Defaults to the
                harness's ``default_channel`` (CLI stdout).
            sleep: dependency-injected sleeper for the delivery retry
                backoff; tests pass a no-op shim to skip the [0,1,4,16] s
                schedule.
        """
        if self._ready:
            return

        scan_dir = self._audit_dir
        if scan_dir is None:
            self._ready = True
            return

        affected = scan_and_autofail(
            scan_dir,
            writer=self._audit,
        )
        channel = recovery_channel or self._default_channel

        for record in affected:
            try:
                summary = build_kernel_restart_summary(
                    trace_id=record.traceId,
                    event_id=record.eventId,
                    user_id=record.userId,
                    command_text=None,
                    affected_task_ids=record.affectedTaskIds,
                )
            except Exception:  # noqa: BLE001 — never let a single bad row block boot
                continue
            try:
                await _delivery.deliver(
                    summary,
                    channel,
                    audit=self._audit,
                    sleep=sleep,
                )
            except DeliveryFailedError:
                # `notification_delivery_failed` already audited inside
                # ``deliver``; the kernel still boots so other traces can
                # be served. Operators consume the audit log to recover.
                continue

        self._ready = True

    async def shutdown(self) -> None:
        """Best-effort teardown: send shutdown frame, then terminate subprocesses."""
        self._heartbeat_tracker.stop()
        if self._heartbeat_runner is not None and not self._heartbeat_runner.done():
            try:
                await asyncio.wait_for(self._heartbeat_runner, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                self._heartbeat_runner.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._heartbeat_runner
        self._heartbeat_runner = None

        for channel in list(self._channels.values()):
            stdin = channel.worker.process.stdin
            if stdin is None or channel.worker.process.returncode is not None:
                continue
            try:
                await write_frame(stdin, ShutdownFrame(kind="shutdown"))
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

        for channel in list(self._channels.values()):
            reader = channel.reader_task
            if reader is not None and not reader.done():
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            monitor = channel.monitor_task
            if monitor is not None and not monitor.done():
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await monitor

        await self._supervisor.shutdown(grace_s=0.5)
        self._channels.clear()
        self._sandboxed_workers.clear()

    # --- submit -----------------------------------------------------------

    async def submit(
        self,
        *,
        text: str,
        user_id: str = "cli-user",
        event_id: str | None = None,
        timeout_s: float = 5.0,
        source_channel: SourceChannel = "cli",
    ) -> TraceResult:
        """Run one event through the full intake -> dispatch -> summary pipeline.

        ``source_channel`` (FR-003) defaults to ``"cli"`` so existing callers
        (Typer command, integration harness) keep their behaviour. The HTTP
        entry stub (T097) and any future channel pass ``"http"`` /
        ``"feishu_stub"`` so the EntryEvent + audit ``event_received`` row
        carry the originating channel verbatim.
        """
        started_at = asyncio.get_running_loop().time()
        eid = event_id if event_id is not None else _new_id()
        actor_user = _user_actor(user_id)
        audit_types: list[str] = []

        def _emit(event: AuditEvent) -> None:
            self._audit.write(event)
            audit_types.append(event.eventType)

        if not self._ready:
            return self._reject_warming_up(
                event_id=eid,
                actor_user=actor_user,
                user_id=user_id,
                started_at=started_at,
                audit_types=audit_types,
                emit=_emit,
            )

        raw = {"text": text, "userId": user_id}
        try:
            assert_payload_size(raw, limit_bytes=self._payload_limit)
        except PayloadTooLarge as exc:
            return self._reject_payload_too_large(
                exc=exc,
                event_id=eid,
                started_at=started_at,
                audit_types=audit_types,
                emit=_emit,
            )

        event = EntryEvent(
            eventId=eid,
            userId=user_id,
            text=text,
            sourceChannel=source_channel,
            receivedAt=datetime.now(tz=UTC),
        )

        rate_check = await self._admit_normal_intake(
            event_id=eid,
            user_id=user_id,
            actor_user=actor_user,
            started_at=started_at,
            audit_types=audit_types,
            emit=_emit,
        )
        if rate_check is not None:
            return rate_check

        planned_leaf = _plan_capability(event.text)
        admitted_risk: Literal["NORMAL", "HIGH_RISK"] = (
            "HIGH_RISK" if planned_leaf.risk_level == "HIGH_RISK" else "NORMAL"
        )
        highrisk_check = await self._admit_highrisk_intake_if_needed(
            admitted_risk=admitted_risk,
            event_id=eid,
            user_id=user_id,
            actor_user=actor_user,
            started_at=started_at,
            audit_types=audit_types,
            emit=_emit,
        )
        if highrisk_check is not None:
            return highrisk_check

        input_hash = hash_canonical_json({"text": event.text, "eventId": event.eventId})

        trace_id, is_replay, cached = self._idempotency.lookup_or_register(
            user_id, eid
        )

        replay_result = await self._handle_replay_if_needed(
            is_replay=is_replay,
            cached=cached,
            trace_id=trace_id,
            event_id=eid,
            user_id=user_id,
            actor_user=actor_user,
            source_channel=source_channel,
            input_hash=input_hash,
            started_at=started_at,
            audit_types=audit_types,
            emit=_emit,
            admitted_risk=admitted_risk,
        )
        if replay_result is not None:
            return replay_result

        return await self._run_fresh_trace(
            event=event,
            trace_id=trace_id,
            event_id=eid,
            user_id=user_id,
            input_hash=input_hash,
            timeout_s=timeout_s,
            admitted_risk=admitted_risk,
            audit_types=audit_types,
            emit=_emit,
            started_at=started_at,
        )

    def _reject_warming_up(
        self,
        *,
        event_id: str,
        actor_user: str,
        user_id: str,
        started_at: float,
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
    ) -> TraceResult:
        emit(
            _build_audit(
                event_type="event_rejected_warming_up",
                actor=actor_user,
                extra={
                    "eventId": event_id,
                    "userId": user_id,
                    "reason": "kernel_warming_up",
                },
            )
        )
        duration = asyncio.get_running_loop().time() - started_at
        return TraceResult(
            traceId="",
            eventId=event_id,
            traceOutcome="rejected",
            leafOutcomes=[],
            audit_event_types=list(audit_types),
            duration_s=duration,
            message="kernel warming up; please retry shortly",
        )

    def _reject_payload_too_large(
        self,
        *,
        exc: PayloadTooLarge,
        event_id: str,
        started_at: float,
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
    ) -> TraceResult:
        emit(build_too_large_audit(exc, user_id=exc.user_id_raw or "system"))
        duration = asyncio.get_running_loop().time() - started_at
        return TraceResult(
            traceId="",
            eventId=event_id,
            traceOutcome="rejected",
            leafOutcomes=[],
            audit_event_types=list(audit_types),
            duration_s=duration,
            message=(
                f"payload too large: {exc.actual_bytes} > {exc.limit_bytes}"
            ),
        )

    async def _admit_normal_intake(
        self,
        *,
        event_id: str,
        user_id: str,
        actor_user: str,
        started_at: float,
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
    ) -> TraceResult | None:
        async with self._rate_lock:
            decision = self._rate_limiter.try_admit(
                user_id=user_id, risk_level="NORMAL"
            )
        if decision.admitted:
            return None
        emit(
            _build_audit(
                event_type="event_rejected_rate_limited",
                actor=actor_user,
                extra={
                    "eventId": event_id,
                    "userId": user_id,
                    "dimension": decision.dimension,
                    "riskLevel": "NORMAL",
                },
            )
        )
        duration = asyncio.get_running_loop().time() - started_at
        return TraceResult(
            traceId="",
            eventId=event_id,
            traceOutcome="rejected",
            leafOutcomes=[],
            audit_event_types=list(audit_types),
            duration_s=duration,
            message=f"rate_limited: dimension={decision.dimension}",
        )

    async def _admit_highrisk_intake_if_needed(
        self,
        *,
        admitted_risk: Literal["NORMAL", "HIGH_RISK"],
        event_id: str,
        user_id: str,
        actor_user: str,
        started_at: float,
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
    ) -> TraceResult | None:
        if admitted_risk != "HIGH_RISK":
            return None
        async with self._rate_lock:
            hr_decision = self._rate_limiter.try_admit_highrisk_only(
                user_id=user_id
            )
        if hr_decision.admitted:
            return None
        emit(
            _build_audit(
                event_type="event_rejected_rate_limited",
                actor=actor_user,
                extra={
                    "eventId": event_id,
                    "userId": user_id,
                    "dimension": hr_decision.dimension,
                    "riskLevel": "HIGH_RISK",
                },
            )
        )
        async with self._rate_lock:
            self._rate_limiter.release(user_id=user_id, risk_level="NORMAL")
        duration = asyncio.get_running_loop().time() - started_at
        return TraceResult(
            traceId="",
            eventId=event_id,
            traceOutcome="rejected",
            leafOutcomes=[],
            audit_event_types=list(audit_types),
            duration_s=duration,
            message=f"rate_limited: dimension={hr_decision.dimension}",
        )

    async def _handle_replay_if_needed(
        self,
        *,
        is_replay: bool,
        cached: CachedTrace | None,
        trace_id: str,
        event_id: str,
        user_id: str,
        actor_user: str,
        source_channel: SourceChannel,
        input_hash: str,
        started_at: float,
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
        admitted_risk: Literal["NORMAL", "HIGH_RISK"],
    ) -> TraceResult | None:
        if not is_replay:
            return None
        emit(
            _build_audit(
                event_type="event_received",
                actor=actor_user,
                trace_id=trace_id,
                input_hash=input_hash,
                idempotent_replay=True,
                extra={"eventId": event_id, "sourceChannel": source_channel},
            )
        )
        emit(
            _build_audit(
                event_type="idempotent_replay",
                actor="kernel",
                trace_id=trace_id,
                idempotent_replay=True,
                extra={
                    "eventId": event_id,
                    "userId": user_id,
                    "isTerminal": bool(cached and cached.is_terminal),
                },
            )
        )
        duration = asyncio.get_running_loop().time() - started_at
        async with self._rate_lock:
            self._rate_limiter.release(user_id=user_id, risk_level=admitted_risk)
        return self._build_replay_result(
            trace_id=trace_id,
            event_id=event_id,
            cached=cached,
            audit_types=audit_types,
            duration=duration,
        )

    async def _run_fresh_trace(
        self,
        *,
        event: EntryEvent,
        trace_id: str,
        event_id: str,
        user_id: str,
        input_hash: str,
        timeout_s: float,
        admitted_risk: Literal["NORMAL", "HIGH_RISK"],
        audit_types: list[str],
        emit: Callable[[AuditEvent], None],
        started_at: float,
    ) -> TraceResult:
        _ = admitted_risk
        emit(
            _build_audit(
                event_type="event_received",
                actor=_user_actor(user_id),
                trace_id=trace_id,
                input_hash=input_hash,
                extra={"eventId": event_id, "sourceChannel": event.sourceChannel},
            )
        )
        emit(
            _build_audit(
                event_type="trace_created",
                actor="kernel",
                trace_id=trace_id,
                extra={"eventId": event_id, "userId": user_id},
            )
        )
        self._cancel_manager.register_trace(trace_id=trace_id, user_id=user_id)
        tree: TaskTree = build_from_event(
            event, trace_id=trace_id, leaves=[_plan_capability(event.text)]
        )
        for task in tree.all_tasks:
            emit(
                _build_audit(
                    event_type="task_created",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=task.taskId,
                    parent_task_id=task.parentTaskId,
                    capability=task.capability,
                    extra={"kind": task.kind},
                )
            )
        leaf_outputs: dict[str, dict[str, Any]] = {}
        terminal_leaves = []
        for leaf in tree.leaves:
            terminal_leaves.append(
                await self._execute_leaf(
                    leaf=leaf,
                    trace_id=trace_id,
                    user_id=user_id,
                    input_hash=input_hash,
                    emit=emit,
                    timeout_s=timeout_s,
                    leaf_outputs=leaf_outputs,
                )
            )
        self._cancel_manager.mark_terminal(trace_id=trace_id)
        summary = build_result_summary(
            trace_id=trace_id,
            event_id=event_id,
            user_id=user_id,
            command_text=event.text,
            leaves=terminal_leaves,
            leaf_outputs=leaf_outputs,
        )
        emit(
            _build_audit(
                event_type="result_summary_prepared",
                actor="kernel",
                trace_id=trace_id,
                extra={"traceOutcome": summary.traceOutcome},
            )
        )
        try:
            await _delivery.deliver(summary, self._default_channel, audit=self._audit)
            audit_types.append("result_summary_delivered")
        except DeliveryFailedError:
            audit_types.append("notification_delivery_failed")
        duration = asyncio.get_running_loop().time() - started_at
        leaf_outcomes = [str(leaf.outcome) for leaf in terminal_leaves]
        self._idempotency.mark_terminal(
            trace_id,
            snapshot={
                "traceOutcome": summary.traceOutcome,
                "leafOutcomes": leaf_outcomes,
                "message": summary.message,
                "deliveryAttempt": summary.deliveryAttempt,
            },
            trace_outcome=summary.traceOutcome,
        )
        self._cancel_manager.release(trace_id=trace_id)
        async with self._rate_lock:
            self._rate_limiter.release(user_id=user_id, risk_level=admitted_risk)
        return TraceResult(
            traceId=trace_id,
            eventId=event_id,
            traceOutcome=summary.traceOutcome,
            leafOutcomes=leaf_outcomes,
            audit_event_types=list(audit_types),
            duration_s=duration,
            message=summary.message,
        )

    def _build_replay_result(
        self,
        *,
        trace_id: str,
        event_id: str,
        cached: CachedTrace | None,
        audit_types: list[str],
        duration: float,
    ) -> TraceResult:
        """Synthesize a ``TraceResult`` for an idempotent replay.

        When the original trace has already reached terminal state we echo
        the cached outcome/message so callers see identical payloads across
        all submits. While still in-flight we return ``"in_flight"`` so
        concurrent duplicate submits do not lie about completion — tests
        assert only on ``traceId`` identity in that case, callers can poll.
        """
        if cached is not None and cached.is_terminal and cached.snapshot is not None:
            snapshot = cached.snapshot
            return TraceResult(
                traceId=trace_id,
                eventId=event_id,
                traceOutcome=str(snapshot.get("traceOutcome", "unknown")),
                leafOutcomes=list(snapshot.get("leafOutcomes", [])),
                audit_event_types=list(audit_types),
                duration_s=duration,
                message=str(snapshot.get("message", "")),
            )
        return TraceResult(
            traceId=trace_id,
            eventId=event_id,
            traceOutcome="in_flight",
            leafOutcomes=[],
            audit_event_types=list(audit_types),
            duration_s=duration,
            message="trace already in flight for this eventId; replay deduped",
        )

    # --- per-leaf execution ---------------------------------------------

    async def _execute_leaf(
        self,
        *,
        leaf: Any,
        trace_id: str,
        user_id: str,
        input_hash: str,
        emit: Any,
        timeout_s: float,
        leaf_outputs: dict[str, dict[str, Any]],
    ) -> Any:
        """Dispatch one leaf to a worker and return the terminal Task.

        HIGH_RISK leaves detour through ``_await_approval`` before the
        dispatcher is consulted; a ``denied`` / ``denied_by_timeout``
        outcome short-circuits to a terminal ``denied`` Task without
        ever asking the dispatcher for a worker.
        """
        pre_leaf = leaf
        if leaf.riskLevel == "HIGH_RISK":
            try:
                decision, gated_task = await self._await_approval(
                    leaf=leaf,
                    trace_id=trace_id,
                    user_id=user_id,
                    emit=emit,
                )
            except _CancelledDuringApproval as cancelled_exc:
                cancelled_task = cancelled_exc.task
                self._cancel_manager.unregister_task(
                    trace_id=trace_id, task_id=cancelled_task.taskId
                )
                emit(
                    _build_audit(
                        event_type="task_cancelled",
                        actor="kernel",
                        trace_id=trace_id,
                        task_id=cancelled_task.taskId,
                        parent_task_id=cancelled_task.parentTaskId,
                        capability=cancelled_task.capability,
                        outcome="cancelled",
                        extra={
                            "failureReason": "user_cancel_before_approval",
                        },
                    )
                )
                return transition(cancelled_task, "cancelled")
            if decision is ApprovalDecision.denied_by_timeout:
                return transition(
                    gated_task,
                    "denied_by_timeout",
                    failure_reason="approval_timeout",
                )
            if decision is ApprovalDecision.denied:
                return transition(
                    gated_task, "denied", failure_reason="user_rejected"
                )
            pre_leaf = gated_task  # pending_approval -> dispatched below

        if self._cancel_manager.is_cancelled(trace_id):
            emit(
                _build_audit(
                    event_type="task_cancelled",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=pre_leaf.taskId,
                    parent_task_id=pre_leaf.parentTaskId,
                    capability=pre_leaf.capability,
                    outcome="cancelled",
                    extra={"failureReason": "user_cancel_before_dispatch"},
                )
            )
            return transition(pre_leaf, "cancelled")

        try:
            handle = self._dispatcher.assign(pre_leaf)
        except NoCapableWorkerError as exc:
            emit(
                _build_audit(
                    event_type="task_failed",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=pre_leaf.taskId,
                    parent_task_id=pre_leaf.parentTaskId,
                    capability=pre_leaf.capability,
                    outcome="failed",
                    extra={
                        "failureReason": "no_capable_worker",
                        "knownCapabilities": list(exc.known_capabilities),
                    },
                )
            )
            return transition(
                pre_leaf, "failed", failure_reason="no_capable_worker"
            )

        channel = self._channels[handle.worker_id]
        assert pre_leaf.budget is not None

        dispatched = transition(pre_leaf, "dispatched")
        self._cancel_manager.register_dispatch(
            trace_id=trace_id,
            task_id=dispatched.taskId,
            worker_id=handle.worker_id,
        )
        # Effective wall budget = tighter of the planner-assigned Task budget
        # and the Worker's capability-registered budget (FR-018). A Worker
        # that advertises a 300 ms cap overrides the planner's 60 s default.
        task_wall_ms = (
            dispatched.budget.wall_clock_ms if dispatched.budget else 60_000
        )
        cap_wall_ms = task_wall_ms
        for capability in handle.capabilities:
            if capability.name == dispatched.capability:
                cap_wall_ms = capability.budget.wall_clock_ms
                break
        wall_ms = min(task_wall_ms, cap_wall_ms)
        deadline = datetime.now(tz=UTC) + timedelta(milliseconds=wall_ms)
        dispatch_frame = DispatchFrame(
            kind="dispatch",
            taskId=dispatched.taskId,
            traceId=trace_id,
            capability=dispatched.capability or "",
            payload=dict(dispatched.payload),
            budget=dispatched.budget,
            deadline=deadline,
        )
        emit(
            _build_audit(
                event_type="task_dispatched",
                actor="kernel",
                trace_id=trace_id,
                task_id=dispatched.taskId,
                parent_task_id=dispatched.parentTaskId,
                capability=dispatched.capability,
                input_hash=input_hash,
                extra={"workerId": handle.worker_id},
            )
        )

        cancel_event = self._cancel_manager.cancel_event(trace_id)
        wall_s = wall_ms / 1000.0
        if timeout_s > 0:
            effective_timeout = min(timeout_s, wall_s)
        else:
            effective_timeout = wall_s
        dispatch_task = asyncio.create_task(
            _dispatch_and_await_result(
                channel,
                dispatch=dispatch_frame,
                timeout_s=effective_timeout,
            )
        )
        try:
            if cancel_event is not None:
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, _pending = await asyncio.wait(
                    {dispatch_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done and dispatch_task not in done:
                    worker = self._supervisor._workers.get(handle.worker_id)
                    escalation = await self._drive_cancel_escalation(
                        trace_id=trace_id,
                        task_id=dispatched.taskId,
                        worker_id=handle.worker_id,
                        channel=channel,
                        worker=worker,
                        emit=emit,
                    )
                    dispatch_task.cancel()
                    try:
                        await dispatch_task
                    except (
                        asyncio.CancelledError,
                        TimeoutError,
                        ProtocolFrameError,
                        RuntimeError,
                    ):
                        pass
                    emit(
                        _build_audit(
                            event_type="task_cancelled",
                            actor="kernel",
                            trace_id=trace_id,
                            task_id=dispatched.taskId,
                            parent_task_id=dispatched.parentTaskId,
                            capability=dispatched.capability,
                            outcome="cancelled",
                            extra={
                                "failureReason": "user_cancel",
                                "escalationStage": escalation.stage.name,
                                "workerId": handle.worker_id,
                            },
                        )
                    )
                    self._cancel_manager.unregister_task(
                        trace_id=trace_id, task_id=dispatched.taskId
                    )
                    return transition(dispatched, "cancelled")
                else:
                    cancel_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancel_task
            _, result_frame = await dispatch_task
        except TimeoutError:
            # Wall-clock budget exhausted (FR-018 / SC-005). The worker has
            # consumed its entire capability ``budget.wall_clock_ms`` without
            # returning a result; we must cleanse the tainted channel so
            # stale frames never bleed into a subsequent dispatch.
            self._tear_down_tainted_worker(handle.worker_id)
            emit(
                _build_audit(
                    event_type="task_failed",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=dispatched.taskId,
                    parent_task_id=dispatched.parentTaskId,
                    capability=dispatched.capability,
                    outcome="failed",
                    extra={
                        "failureReason": "budget_exceeded",
                        "failureDim": "wall",
                        "workerId": handle.worker_id,
                    },
                )
            )
            self._cancel_manager.unregister_task(
                trace_id=trace_id, task_id=dispatched.taskId
            )
            return transition(
                dispatched,
                "failed",
                failure_reason="budget_exceeded",
                failure_dim="wall",
            )
        except (ProtocolFrameError, RuntimeError, OSError) as exc:
            # If the resource monitor had already marked this worker as
            # sandboxed (T074 / FR-025), map the channel-EOF to
            # ``sandbox_limit`` so the audit + failure reason reflect the
            # real root cause instead of a generic crash.
            is_sandbox = handle.worker_id in self._sandboxed_workers
            failure_reason: Literal["sandbox_limit", "worker_crashed"] = (
                "sandbox_limit" if is_sandbox else "worker_crashed"
            )
            extra: dict[str, Any] = {
                "failureReason": failure_reason,
                "error": repr(exc),
                "workerId": handle.worker_id,
            }
            if is_sandbox:
                self._sandboxed_workers.discard(handle.worker_id)
            emit(
                _build_audit(
                    event_type="task_failed",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=dispatched.taskId,
                    parent_task_id=dispatched.parentTaskId,
                    capability=dispatched.capability,
                    outcome="failed",
                    extra=extra,
                )
            )
            self._tear_down_tainted_worker(handle.worker_id)
            self._cancel_manager.unregister_task(
                trace_id=trace_id, task_id=dispatched.taskId
            )
            return transition(
                dispatched, "failed", failure_reason=failure_reason
            )

        running = transition(dispatched, "running")
        emit(
            _build_audit(
                event_type="task_started",
                actor=f"worker:{handle.worker_id}",
                trace_id=trace_id,
                task_id=running.taskId,
                parent_task_id=running.parentTaskId,
                capability=running.capability,
            )
        )

        if result_frame.outcome == "succeeded":
            output_hash = None
            if result_frame.output is not None:
                output_hash = hash_canonical_json(result_frame.output)
                leaf_outputs[running.taskId] = dict(result_frame.output)
            emit(
                _build_audit(
                    event_type="task_succeeded",
                    actor=f"worker:{handle.worker_id}",
                    trace_id=trace_id,
                    task_id=running.taskId,
                    parent_task_id=running.parentTaskId,
                    capability=running.capability,
                    outcome="succeeded",
                    output_hash=output_hash,
                )
            )
            self._cancel_manager.unregister_task(
                trace_id=trace_id, task_id=running.taskId
            )
            return transition(running, "succeeded")

        # failed branch
        emit(
            _build_audit(
                event_type="task_failed",
                actor=f"worker:{handle.worker_id}",
                trace_id=trace_id,
                task_id=running.taskId,
                parent_task_id=running.parentTaskId,
                capability=running.capability,
                outcome="failed",
                extra={
                    "failureReason": result_frame.failureReason
                    or "worker_internal_error",
                    "failureDim": result_frame.failureDim,
                },
            )
        )
        self._cancel_manager.unregister_task(
            trace_id=trace_id, task_id=running.taskId
        )
        return transition(
            running,
            "failed",
            failure_reason=_map_worker_failure(result_frame.failureReason),
            failure_dim=result_frame.failureDim,
        )

    # --- cancel flow -----------------------------------------------------

    async def request_cancel(
        self, *, trace_id: str, user_id: str
    ) -> CancelOutcome:
        """Accept an external cancel request against a live trace.

        The audit chain is written here (not inside ``CancelManager``)
        so the single source of truth for event order stays in the
        harness. Impersonation, terminal, unknown, and repeat cases
        each emit exactly one audit event; ``accepted`` emits the
        follow-up ``soft_abort_sent`` for each worker we actually
        signalled.
        """
        outcome = await self._cancel_manager.request_cancel(
            trace_id=trace_id, user_id=user_id
        )
        actor = _user_actor(user_id)
        if outcome.status is CancelStatus.not_found:
            self._audit.write(
                _build_audit(
                    event_type="cancel_not_found",
                    actor=actor,
                    trace_id=trace_id,
                    extra={"userId": user_id},
                )
            )
            return outcome
        if outcome.status is CancelStatus.impersonation_rejected:
            self._audit.write(
                _build_audit(
                    event_type="cancel_not_found",
                    actor=actor,
                    trace_id=trace_id,
                    extra={
                        "userId": user_id,
                        "reason": "impersonation_rejected",
                    },
                )
            )
            return outcome
        if outcome.status is CancelStatus.already_cancelled:
            self._audit.write(
                _build_audit(
                    event_type="cancel_late",
                    actor=actor,
                    trace_id=trace_id,
                    extra={"userId": user_id, "reason": "already_cancelled"},
                )
            )
            return outcome
        if outcome.status is CancelStatus.already_terminal:
            self._audit.write(
                _build_audit(
                    event_type="cancel_late",
                    actor=actor,
                    trace_id=trace_id,
                    extra={"userId": user_id, "reason": "already_terminal"},
                )
            )
            return outcome

        self._audit.write(
            _build_audit(
                event_type="cancel_requested",
                actor=actor,
                trace_id=trace_id,
                extra={
                    "userId": user_id,
                    "taskCount": len(outcome.cancelled_task_ids),
                    "workerCount": len(outcome.cancelled_worker_ids),
                },
            )
        )
        return outcome

    async def _drive_cancel_escalation(
        self,
        *,
        trace_id: str,
        task_id: str,
        worker_id: str,
        channel: _WorkerChannel,
        worker: SupervisedWorker | None,
        emit: Any,
    ) -> Any:
        """Send AbortFrame + run the soft→terminate→kill escalation.

        Emits ``soft_abort_sent`` (and ``hard_abort_sent`` if the
        escalation actually reaches ``kill()``) so spec.md FR-014 has
        an auditable trail per spec.md §P4 Scenario 1.
        """
        abort_frame = AbortFrame(
            kind="abort", taskId=task_id, reason="user_cancel"
        )

        async def _send_soft() -> None:
            try:
                async with channel.lock:
                    stdin = channel.worker.process.stdin
                    if stdin is not None:
                        await write_frame(stdin, abort_frame)
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

        emit(
            _build_audit(
                event_type="soft_abort_sent",
                actor="kernel",
                trace_id=trace_id,
                task_id=task_id,
                extra={"workerId": worker_id, "reason": "user_cancel"},
            )
        )

        if worker is None:
            # Worker already reaped; nothing to escalate against.
            return _FakeEscalation(EscalationStage.already_terminal)

        result = await soft_abort_with_escalation(
            process=worker.process,
            send_soft_signal=_send_soft,
            soft_timeout_s=self._cancel_soft_timeout_s,
            hard_timeout_s=self._cancel_hard_timeout_s,
        )
        if result.stage is EscalationStage.kill:
            emit(
                _build_audit(
                    event_type="hard_abort_sent",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=task_id,
                    extra={"workerId": worker_id, "reason": "user_cancel"},
                )
            )
        return result

    # --- approval flow ---------------------------------------------------

    async def _await_approval(
        self,
        *,
        leaf: Any,
        trace_id: str,
        user_id: str,
        emit: Any,
    ) -> tuple[ApprovalDecision, Any]:
        """Park ``leaf`` in ``pending_approval`` and block until resolved.

        Returns ``(decision, task_in_pending_approval_state)``. For
        ``approved`` the returned Task sits in ``pending_approval`` so
        the caller's subsequent ``transition(task, "dispatched")`` stays
        within the state-machine table.
        """
        entry = self._approval_gate.register(
            task=leaf,
            user_id=user_id,
            window_ms=self._approval_timeout_ms,
        )
        pending_task = transition(leaf, "pending_approval")
        self._cancel_manager.register_dispatch(
            trace_id=trace_id, task_id=pending_task.taskId, worker_id=None
        )

        event = asyncio.Event()
        async with self._approval_lock:
            self._approval_events[trace_id] = event

        emit(
            _build_audit(
                event_type="task_pending_approval",
                actor="kernel",
                trace_id=trace_id,
                task_id=pending_task.taskId,
                parent_task_id=pending_task.parentTaskId,
                capability=pending_task.capability,
                extra={
                    "userId": user_id,
                    "expiresAt": entry.expires_at.isoformat(),
                    "summary": entry.request.summary,
                },
            )
        )

        timeout_s = max(self._approval_timeout_ms / 1000.0, 0.001)
        cancel_event = self._cancel_manager.cancel_event(trace_id)
        try:
            if cancel_event is not None:
                done, _ = await asyncio.wait(
                    {
                        asyncio.create_task(event.wait()),
                        asyncio.create_task(cancel_event.wait()),
                    },
                    timeout=timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Drain to avoid un-awaited coroutine warnings.
                for t in done:
                    t.result()
            else:
                await asyncio.wait_for(event.wait(), timeout=timeout_s)
        except TimeoutError:
            pass
        finally:
            async with self._approval_lock:
                self._approval_events.pop(trace_id, None)

        if self._cancel_manager.is_cancelled(trace_id):
            # Drop the pending entry so a late user reply doesn't fire
            # `approval_granted` for a cancelled trace.
            if self._approval_gate.is_pending(pending_task.taskId):
                self._approval_gate._by_task.pop(pending_task.taskId, None)
                self._approval_gate._by_trace.pop(trace_id, None)
            raise _CancelledDuringApproval(pending_task)

        outcome_decision = self._approval_gate.sweep_expired()
        for outcome in outcome_decision:
            if outcome.task_id == pending_task.taskId:
                emit(
                    _build_audit(
                        event_type="approval_timeout",
                        actor="kernel",
                        trace_id=trace_id,
                        task_id=pending_task.taskId,
                        parent_task_id=pending_task.parentTaskId,
                        capability=pending_task.capability,
                        outcome="denied_by_timeout",
                        extra={"userId": user_id},
                    )
                )
                return ApprovalDecision.denied_by_timeout, pending_task

        resolved = getattr(event, "_resolved_decision", None)
        resolved_user = getattr(event, "_resolved_user_id", None)
        if resolved is ApprovalDecision.approved:
            emit(
                _build_audit(
                    event_type="approval_granted",
                    actor=_user_actor(resolved_user or user_id),
                    trace_id=trace_id,
                    task_id=pending_task.taskId,
                    parent_task_id=pending_task.parentTaskId,
                    capability=pending_task.capability,
                    outcome="succeeded",
                    extra={"userId": resolved_user or user_id},
                )
            )
            return ApprovalDecision.approved, pending_task
        if resolved is ApprovalDecision.denied:
            emit(
                _build_audit(
                    event_type="approval_denied",
                    actor=_user_actor(resolved_user or user_id),
                    trace_id=trace_id,
                    task_id=pending_task.taskId,
                    parent_task_id=pending_task.parentTaskId,
                    capability=pending_task.capability,
                    outcome="denied",
                    extra={"userId": resolved_user or user_id},
                )
            )
            return ApprovalDecision.denied, pending_task

        emit(
            _build_audit(
                event_type="approval_timeout",
                actor="kernel",
                trace_id=trace_id,
                task_id=pending_task.taskId,
                parent_task_id=pending_task.parentTaskId,
                capability=pending_task.capability,
                outcome="denied_by_timeout",
                extra={"userId": user_id},
            )
        )
        return ApprovalDecision.denied_by_timeout, pending_task

    async def submit_approval_response(
        self,
        *,
        trace_id: str,
        decision: str,
        user_id: str,
    ) -> None:
        """Inject an ``ApprovalResponse`` into the kernel (test / CLI hook).

        Foreign userIds audit ``approval_impersonation_rejected`` and do
        NOT release the pending submit — the legitimate user can still
        approve afterwards (FR-012).
        """
        if decision not in {"approve", "deny"}:
            raise ValueError(
                f"decision must be 'approve' or 'deny'; got {decision!r}"
            )
        response = ApprovalResponse.model_validate(
            {
                "kind": "approval_response",
                "traceId": trace_id,
                "decision": decision,
                "userId": user_id,
                "receivedAt": datetime.now(tz=UTC),
            }
        )
        try:
            outcome = self._approval_gate.handle_response(response)
        except ApprovalStaleError:
            self._audit.write(
                _build_audit(
                    event_type="approval_stale",
                    actor=_user_actor(user_id),
                    trace_id=trace_id,
                    extra={"userId": user_id, "decision": decision},
                )
            )
            return

        if outcome.decision is ApprovalDecision.impersonation_rejected:
            self._audit.write(
                _build_audit(
                    event_type="approval_impersonation_rejected",
                    actor=_user_actor(user_id),
                    trace_id=trace_id,
                    task_id=outcome.task_id,
                    extra={
                        "attemptedBy": user_id,
                        "expectedUser": "<redacted>",
                        "decision": decision,
                    },
                )
            )
            return

        async with self._approval_lock:
            event = self._approval_events.get(trace_id)
        if event is None:
            return
        event._resolved_decision = outcome.decision  # type: ignore[attr-defined]
        event._resolved_user_id = outcome.user_id  # type: ignore[attr-defined]
        event.set()

    @staticmethod
    def _trace_outcome_to_audit_outcome(trace_outcome: str) -> AuditOutcome | None:
        mapping: dict[str, AuditOutcome] = {
            "all_succeeded": "succeeded",
            "all_failed": "failed",
            "partial_failed": "failed",
            "cancelled": "cancelled",
            "denied": "denied",
            "rejected": "rejected",
        }
        return mapping.get(trace_outcome)

    @staticmethod
    def _normalize_clock(
        clock: Callable[..., datetime] | None,
    ) -> Callable[[], datetime] | None:
        """Adapt the few clock shapes used across rate-limit tests.

        ``RateLimiter`` expects a zero-argument callable returning ``datetime``.
        Older tests / callers may still pass a ``(datetime) -> datetime`` style
        helper, so we normalize both forms here instead of scattering casts or
        type ignores through the constructor.
        """
        if clock is None:
            return None
        try:
            from inspect import signature

            params = signature(clock).parameters
        except (TypeError, ValueError):
            return None if clock is None else (lambda: datetime.now(tz=UTC))
        if len(params) == 0:
            return clock
        if len(params) == 1:
            return lambda: clock(datetime.now(tz=UTC))
        return None


def _map_worker_failure(reason: str | None) -> Any:
    """Map Worker-side failure reasons onto the Task-level FailureReason enum.

    The wire contract (``WorkerFailureReason``) only exposes worker-scoped
    reasons; Task-side audit uses a broader vocabulary. For US1 we collapse:

    - ``budget_exceeded`` -> ``budget_exceeded``
    - everything else / None -> ``worker_crashed``
    """
    if reason == "budget_exceeded":
        return "budget_exceeded"
    return "worker_crashed"


# ---------------------------------------------------------------------------
# Public assembly entry point.
# ---------------------------------------------------------------------------


async def assemble_kernel(
    *,
    audit_dir: Path,
    payload_limit_bytes: int = DEFAULT_PAYLOAD_MAX_BYTES,
    approval_timeout_ms: int = DEFAULT_APPROVAL_WINDOW_MS,
    cancel_soft_timeout_s: float = 3.0,
    cancel_hard_timeout_s: float = 1.0,
    warm_start: bool = True,
    default_channel: DeliveryChannel | None = None,
    rate_limits: RateLimits | None = None,
    rate_limiter_clock: Callable[[], datetime] | None = None,
) -> KernelHarness:
    """Construct a fully wired ``KernelHarness`` against ``audit_dir``.

    ``audit_dir`` is created if missing. All other subsystems use their
    defaults; tests wanting to inject a clock or opener can subclass the
    harness or instantiate ``KernelHarness`` directly.

    ``approval_timeout_ms`` overrides the default 10-minute HIGH_RISK
    approval window (FR-011); integration tests drive short windows
    (500 ms) to exercise the ``denied_by_timeout`` branch without real
    sleep.
    """
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    writer = AuditWriter(audit_dir)
    dispatcher = Dispatcher()
    supervisor = WorkerSupervisor()
    return KernelHarness(
        audit_writer=writer,
        dispatcher=dispatcher,
        supervisor=supervisor,
        payload_limit_bytes=payload_limit_bytes,
        approval_timeout_ms=approval_timeout_ms,
        cancel_soft_timeout_s=cancel_soft_timeout_s,
        cancel_hard_timeout_s=cancel_hard_timeout_s,
        audit_dir=audit_dir,
        warm_start=warm_start,
        default_channel=default_channel,
        rate_limits=rate_limits,
        rate_limiter_clock=rate_limiter_clock,
    )


# ---------------------------------------------------------------------------
# CLI wiring — the actual command bodies live in `.entrypoints.cli` (T044).
# ---------------------------------------------------------------------------

# Import side effect: register `submit` + `status` subcommands on `app`.
from .entrypoints.cli import register as _register_cli_commands  # noqa: E402

_register_cli_commands(app)


if __name__ == "__main__":
    app()


__all__ = [
    "KernelHarness",
    "TraceResult",
    "app",
    "assemble_kernel",
]
