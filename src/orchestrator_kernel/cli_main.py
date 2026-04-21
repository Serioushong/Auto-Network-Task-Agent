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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
import ulid

from .audit.hasher import hash_canonical_json
from .audit.writer import AuditWriter
from .contracts.audit import AuditEvent, AuditEventType, AuditOutcome
from .contracts.entry_event import EntryEvent
from .contracts.worker_protocol import (
    DispatchFrame,
    RegisterFrame,
    ResultFrame,
    ShutdownFrame,
    StartedFrame,
)
from .kernel.dispatcher import Dispatcher, NoCapableWorkerError, WorkerHandle
from .kernel.idempotency import CachedTrace, IdempotencyCache
from .kernel.state_machine import transition
from .kernel.task_tree import LeafPlan, TaskTree, build_from_event
from .kernel.validators import (
    DEFAULT_PAYLOAD_MAX_BYTES,
    PayloadTooLarge,
    _is_actor_safe,
    assert_payload_size,
    build_too_large_audit,
)
from .notifier.result_summary import build_result_summary, print_to_cli
from .worker_supervisor.protocol import (
    ProtocolFrameError,
    decode_frame,
    encode_frame,
    write_frame,
)
from .worker_supervisor.supervisor import SupervisedWorker, WorkerSupervisor

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
class _WorkerChannel:
    """Couples a ``SupervisedWorker`` with its registration + stdio lock.

    The lock serialises dispatch round-trips per worker — US1 MVP only
    needs one in-flight task per worker, and a lock is the smallest
    primitive that keeps "write dispatch, read started, read result"
    atomic against future concurrent callers.
    """

    worker: SupervisedWorker
    handle: WorkerHandle
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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


async def _dispatch_and_await_result(
    channel: _WorkerChannel,
    *,
    dispatch: DispatchFrame,
    timeout_s: float,
) -> tuple[StartedFrame, ResultFrame]:
    """Serialise ``dispatch -> started -> result`` under the channel lock.

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

    deadline = asyncio.get_running_loop().time() + timeout_s
    async with channel.lock:
        worker.process.stdin.write(encode_frame(dispatch))
        await worker.process.stdin.drain()

        started: StartedFrame | None = None
        result: ResultFrame | None = None
        while result is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"worker {worker.worker_id!r} did not finish dispatch "
                    f"within {timeout_s}s"
                )
            raw = await _read_one_frame_bytes(worker, timeout_s=remaining)
            frame = decode_frame(raw)
            if isinstance(frame, StartedFrame):
                started = frame
            elif isinstance(frame, ResultFrame):
                result = frame
            else:
                # Heartbeats / anything else: ignore; don't fail the caller.
                continue

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
    ) -> None:
        self._audit = audit_writer
        self._dispatcher = dispatcher
        self._supervisor = supervisor
        self._payload_limit = payload_limit_bytes
        self._channels: dict[str, _WorkerChannel] = {}
        self._idempotency = idempotency_cache or IdempotencyCache()

    # --- lifecycle ---------------------------------------------------------

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
        self._dispatcher.register(handle)
        self._channels[handle.worker_id] = _WorkerChannel(worker=worker, handle=handle)

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

    async def shutdown(self) -> None:
        """Best-effort teardown: send shutdown frame, then terminate subprocesses."""
        for channel in list(self._channels.values()):
            stdin = channel.worker.process.stdin
            if stdin is None or channel.worker.process.returncode is not None:
                continue
            try:
                await write_frame(stdin, ShutdownFrame(kind="shutdown"))
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
        await self._supervisor.shutdown(grace_s=0.5)
        self._channels.clear()

    # --- submit -----------------------------------------------------------

    async def submit(
        self,
        *,
        text: str,
        user_id: str = "cli-user",
        event_id: str | None = None,
        timeout_s: float = 5.0,
    ) -> TraceResult:
        """Run one event through the full intake -> dispatch -> summary pipeline."""
        started_at = asyncio.get_running_loop().time()
        eid = event_id if event_id is not None else _new_id()
        actor_user = _user_actor(user_id)
        audit_types: list[str] = []

        def _emit(event: AuditEvent) -> None:
            self._audit.write(event)
            audit_types.append(event.eventType)

        raw = {"text": text, "userId": user_id}
        try:
            assert_payload_size(raw, limit_bytes=self._payload_limit)
        except PayloadTooLarge as exc:
            audit = build_too_large_audit(exc, user_id=user_id)
            _emit(audit)
            duration = asyncio.get_running_loop().time() - started_at
            return TraceResult(
                traceId="",
                eventId=eid,
                traceOutcome="rejected",
                leafOutcomes=[],
                audit_event_types=list(audit_types),
                duration_s=duration,
                message=f"payload too large: {exc.actual_bytes} > {exc.limit_bytes}",
            )

        event = EntryEvent(
            eventId=eid,
            userId=user_id,
            text=text,
            sourceChannel="cli",
            receivedAt=datetime.now(tz=UTC),
        )

        input_hash = hash_canonical_json({"text": event.text, "eventId": event.eventId})

        trace_id, is_replay, cached = self._idempotency.lookup_or_register(
            user_id, eid
        )

        if is_replay:
            _emit(
                _build_audit(
                    event_type="event_received",
                    actor=actor_user,
                    trace_id=trace_id,
                    input_hash=input_hash,
                    idempotent_replay=True,
                    extra={"eventId": eid, "sourceChannel": "cli"},
                )
            )
            _emit(
                _build_audit(
                    event_type="idempotent_replay",
                    actor="kernel",
                    trace_id=trace_id,
                    idempotent_replay=True,
                    extra={
                        "eventId": eid,
                        "userId": user_id,
                        "isTerminal": bool(cached and cached.is_terminal),
                    },
                )
            )
            duration = asyncio.get_running_loop().time() - started_at
            return self._build_replay_result(
                trace_id=trace_id,
                event_id=eid,
                cached=cached,
                audit_types=audit_types,
                duration=duration,
            )

        _emit(
            _build_audit(
                event_type="event_received",
                actor=actor_user,
                trace_id=trace_id,
                input_hash=input_hash,
                extra={"eventId": eid, "sourceChannel": "cli"},
            )
        )
        _emit(
            _build_audit(
                event_type="trace_created",
                actor="kernel",
                trace_id=trace_id,
                extra={"eventId": eid, "userId": user_id},
            )
        )

        plan = _plan_capability(event.text)
        tree: TaskTree = build_from_event(
            event, trace_id=trace_id, leaves=[plan]
        )

        for task in tree.all_tasks:
            _emit(
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
            terminal = await self._execute_leaf(
                leaf=leaf,
                trace_id=trace_id,
                input_hash=input_hash,
                emit=_emit,
                timeout_s=timeout_s,
                leaf_outputs=leaf_outputs,
            )
            terminal_leaves.append(terminal)

        summary = build_result_summary(
            trace_id=trace_id,
            event_id=eid,
            user_id=user_id,
            command_text=event.text,
            leaves=terminal_leaves,
            leaf_outputs=leaf_outputs,
        )
        _emit(
            _build_audit(
                event_type="result_summary_prepared",
                actor="kernel",
                trace_id=trace_id,
                extra={"traceOutcome": summary.traceOutcome},
            )
        )
        print_to_cli(summary)
        _emit(
            _build_audit(
                event_type="result_summary_delivered",
                actor="kernel",
                trace_id=trace_id,
                outcome=self._trace_outcome_to_audit_outcome(summary.traceOutcome),
                extra={
                    "traceOutcome": summary.traceOutcome,
                    "deliveryAttempt": summary.deliveryAttempt,
                },
            )
        )

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
        return TraceResult(
            traceId=trace_id,
            eventId=eid,
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
        input_hash: str,
        emit: Any,
        timeout_s: float,
        leaf_outputs: dict[str, dict[str, Any]],
    ) -> Any:
        """Dispatch one leaf to a worker and return the terminal Task."""
        try:
            handle = self._dispatcher.assign(leaf)
        except NoCapableWorkerError as exc:
            emit(
                _build_audit(
                    event_type="task_failed",
                    actor="kernel",
                    trace_id=trace_id,
                    task_id=leaf.taskId,
                    parent_task_id=leaf.parentTaskId,
                    capability=leaf.capability,
                    outcome="failed",
                    extra={
                        "failureReason": "no_capable_worker",
                        "knownCapabilities": list(exc.known_capabilities),
                    },
                )
            )
            return transition(
                leaf, "failed", failure_reason="no_capable_worker"
            )

        channel = self._channels[handle.worker_id]
        assert leaf.budget is not None

        dispatched = transition(leaf, "dispatched")
        wall_ms = dispatched.budget.wall_clock_ms if dispatched.budget else 60_000
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

        try:
            _, result_frame = await _dispatch_and_await_result(
                channel, dispatch=dispatch_frame, timeout_s=timeout_s
            )
        except (TimeoutError, ProtocolFrameError, RuntimeError) as exc:
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
                        "failureReason": "worker_crashed",
                        "error": repr(exc),
                    },
                )
            )
            return transition(
                dispatched, "failed", failure_reason="worker_crashed"
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
        return transition(
            running,
            "failed",
            failure_reason=_map_worker_failure(result_frame.failureReason),
            failure_dim=result_frame.failureDim,
        )

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
) -> KernelHarness:
    """Construct a fully wired ``KernelHarness`` against ``audit_dir``.

    ``audit_dir`` is created if missing. All other subsystems use their
    defaults; tests wanting to inject a clock or opener can subclass the
    harness or instantiate ``KernelHarness`` directly.
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
