"""T068 — Signal escalation helper (FR-014 / spec.md US5 Scenario 3).

``soft_abort_with_escalation`` drives the mandatory 3-tier cancel timing:

    0 s   — send cooperative soft signal (caller-provided coroutine:
            AbortFrame via stdin, then SIGBREAK / SIGTERM via proc group);
    +3 s  — if the worker has not exited, call ``process.terminate()``
            (Windows: `TerminateProcess` via asyncio; POSIX: SIGTERM);
    +1 s  — if still alive, call ``process.kill()`` (SIGKILL /
            TerminateProcess-9);
    done — return a ``TerminationResult`` describing which stage
            actually terminated the worker.

The helper is deliberately decoupled from any kernel bookkeeping: the
caller supplies the ``send_soft_signal`` awaitable and the process
object; the audit events / Task-state transitions live in
``KernelHarness.request_cancel``. Keeping the escalation logic pure
makes ``tests/unit/test_cancel_signal_escalation.py`` deterministic
(no real subprocess, fake clock).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

import psutil

__all__ = [
    "EscalationStage",
    "HeartbeatTracker",
    "ProcessLike",
    "ResourceLimitsSnapshot",
    "TerminationResult",
    "Violation",
    "check_limits",
    "soft_abort_with_escalation",
    "watch_worker",
]


class EscalationStage(IntEnum):
    """Which stage in the escalation ladder actually ended the worker."""

    already_terminal = 0
    soft = 1
    terminate = 2
    kill = 3


@dataclass(frozen=True)
class TerminationResult:
    """Outcome of one call to ``soft_abort_with_escalation``."""

    stage: EscalationStage
    exit_code: int | None
    elapsed_s: float


class ProcessLike(Protocol):
    """Subset of ``asyncio.subprocess.Process`` we actually depend on.

    ``asyncio.subprocess.Process`` satisfies this by duck typing; a
    ``_FakeProcess`` in tests satisfies it too. The helper never imports
    ``asyncio.subprocess.Process`` so it can be unit-tested without
    spawning a subprocess.
    """

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


async def soft_abort_with_escalation(
    *,
    process: ProcessLike,
    send_soft_signal: Callable[[], Awaitable[None]],
    soft_timeout_s: float = 3.0,
    hard_timeout_s: float = 1.0,
) -> TerminationResult:
    """Escalate worker termination through soft -> terminate -> kill.

    Args:
        process: Handle exposing ``returncode``, ``terminate``,
            ``kill``, ``await wait()``.
        send_soft_signal: Awaitable that, when awaited, delivers the
            cooperative abort (AbortFrame + SIGBREAK / SIGTERM). MUST
            return an awaitable; sync callables are rejected with
            ``TypeError`` so bugs surface at call site rather than by
            silently skipping the soft stage.
        soft_timeout_s: Seconds to wait for voluntary exit after the
            soft signal before calling ``terminate()``. FR-014 = 3.
        hard_timeout_s: Seconds to wait after ``terminate()`` before
            calling ``kill()``. FR-014 does not name this window; we
            keep it short (1 s) so the kernel's ≤ 5 s budget in
            FR-013 stays satisfied across a clean escalation:
            soft (0) + soft_timeout (3) + hard_timeout (1) = 4 s.

    Returns:
        A ``TerminationResult`` whose ``stage`` reports which verb
        finally ended the worker.

    Raises:
        TypeError: ``send_soft_signal`` is not a coroutine callable.
    """
    if not inspect.iscoroutinefunction(send_soft_signal):
        raise TypeError(
            "send_soft_signal MUST be an awaitable callable (async def)"
        )

    loop = asyncio.get_running_loop()
    started = loop.time()

    if process.returncode is not None:
        return TerminationResult(
            stage=EscalationStage.already_terminal,
            exit_code=process.returncode,
            elapsed_s=0.0,
        )

    await send_soft_signal()
    try:
        exit_code = await asyncio.wait_for(
            process.wait(), timeout=soft_timeout_s
        )
    except TimeoutError:
        pass
    else:
        return TerminationResult(
            stage=EscalationStage.soft,
            exit_code=exit_code,
            elapsed_s=loop.time() - started,
        )

    try:
        process.terminate()
    except ProcessLookupError:
        pass

    try:
        exit_code = await asyncio.wait_for(
            process.wait(), timeout=hard_timeout_s
        )
    except TimeoutError:
        pass
    else:
        return TerminationResult(
            stage=EscalationStage.terminate,
            exit_code=exit_code,
            elapsed_s=loop.time() - started,
        )

    try:
        process.kill()
    except ProcessLookupError:
        pass

    final_code: int | None
    try:
        final_code = await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        final_code = None

    return TerminationResult(
        stage=EscalationStage.kill,
        exit_code=final_code,
        elapsed_s=loop.time() - started,
    )


# ---------------------------------------------------------------------------
# T073 — Resource monitor (FR-025 / SC-005).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimitsSnapshot:
    """Flattened bounds fed into ``check_limits`` from a ``WorkerRegistration``.

    We keep this decoupled from the pydantic ``ResourceLimits`` contract so
    the sampler can be unit-tested without importing the full worker schema.
    ``memory_mb`` / ``cpu_pct`` mirror the registered envelope exactly.
    """

    memory_mb: int
    cpu_pct: int


@dataclass(frozen=True)
class Violation:
    """One sandbox-limit breach emitted by ``check_limits``."""

    dimension: str  # "memory_mb" | "cpu_pct"
    actual: float
    limit: float


def _psutil_process(pid: int) -> psutil.Process:
    """Indirection used by tests to monkeypatch the ``psutil.Process`` ctor."""
    return psutil.Process(pid)


def check_limits(
    *, pid: int, limits: ResourceLimitsSnapshot
) -> Violation | None:
    """Sample one worker and report a violation on any breached dimension.

    Returns ``None`` when the worker is under budget *and* when the process
    has already disappeared (NoSuchProcess). A disappeared process is
    treated as benign because the supervisor is about to surface the exit
    through the regular ``SupervisedWorker`` channel; double-counting it
    here as a violation would race with ``worker_crashed``.
    """
    try:
        proc = _psutil_process(pid)
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=None)
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        return None

    if rss_mb > limits.memory_mb:
        return Violation(
            dimension="memory_mb", actual=rss_mb, limit=limits.memory_mb
        )
    # We flag CPU only when the sampled value is strictly higher than the
    # configured envelope; psutil returns 0.0 on the first call which must
    # not be mistaken for a violation.
    if cpu_pct > 0.0 and cpu_pct > limits.cpu_pct:
        return Violation(
            dimension="cpu_pct", actual=cpu_pct, limit=limits.cpu_pct
        )
    return None


# ---------------------------------------------------------------------------
# T076 — Heartbeat tracker (FR-009 / spec.md §FR-009).
# ---------------------------------------------------------------------------


HeartbeatCallback = Callable[[str], Any]
"""Unhealthy / recovered hook. Accepts a ``worker_id``; may return Awaitable or None."""


async def _maybe_await(
    callback: HeartbeatCallback | None, worker_id: str
) -> None:
    """Invoke a callback that may be sync or async; swallow ``None`` returns."""
    if callback is None:
        return
    result = callback(worker_id)
    if inspect.isawaitable(result):
        await result


class HeartbeatTracker:
    """Per-worker liveness watchdog (FR-009).

    The kernel starts one tracker when the harness boots and ``track()``s
    each worker after it registers. The tracker is the single source of
    truth for *worker health*; when a worker misses
    ``miss_threshold * interval_s`` seconds of heartbeats the tracker
    fires ``on_unhealthy(worker_id)``. A subsequent heartbeat fires
    ``on_recovered``. The tracker never flips the dispatcher directly —
    callers wire the callbacks to ``Dispatcher.set_health()``.

    The tracker is deliberately clock-injectable: real code passes
    ``time.monotonic`` and real heartbeat frames; tests pass a mutable
    ``_FakeClock`` so the stale-threshold logic is deterministic without
    ``asyncio.sleep`` hackery. See
    ``tests/unit/test_heartbeat_tracker.py``.

    Thread safety: single-owner (the kernel event loop). The ``track`` /
    ``feed`` / ``untrack`` methods are plain dict ops, the ``run()``
    coroutine is the only asynchronous surface.
    """

    def __init__(
        self,
        *,
        interval_s: float = 0.5,
        miss_threshold: int = 3,
        on_unhealthy: HeartbeatCallback | None = None,
        on_recovered: HeartbeatCallback | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        if miss_threshold < 1:
            raise ValueError("miss_threshold must be >= 1")
        self._interval_s = float(interval_s)
        self._miss_threshold = int(miss_threshold)
        self._on_unhealthy = on_unhealthy
        self._on_recovered = on_recovered
        self._now = now or time.monotonic
        self._last_seen: dict[str, float] = {}
        self._unhealthy: set[str] = set()
        self._stop = asyncio.Event()

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def miss_threshold(self) -> int:
        return self._miss_threshold

    def track(self, worker_id: str) -> None:
        """Begin observing ``worker_id``. Resets last-seen to ``now()``."""
        self._last_seen[worker_id] = self._now()

    def untrack(self, worker_id: str) -> None:
        """Stop observing ``worker_id``. Subsequent feeds are ignored."""
        self._last_seen.pop(worker_id, None)
        self._unhealthy.discard(worker_id)

    def feed(self, worker_id: str) -> None:
        """Record a live heartbeat.

        Unknown workers are silently ignored (prevents a race where a
        worker has been ``untrack()``ed but one last heartbeat was still
        in flight). If the worker is currently flagged unhealthy the
        tracker fires ``on_recovered`` exactly once.
        """
        if worker_id not in self._last_seen:
            return
        self._last_seen[worker_id] = self._now()
        if worker_id in self._unhealthy:
            self._unhealthy.discard(worker_id)
            asyncio.get_event_loop().create_task(
                _maybe_await(self._on_recovered, worker_id),
                name=f"heartbeat-recovered-{worker_id}",
            )

    def stop(self) -> None:
        """Ask the ``run()`` loop to exit on its next tick."""
        self._stop.set()

    def is_unhealthy(self, worker_id: str) -> bool:
        return worker_id in self._unhealthy

    async def run(self) -> None:
        """Poll every tracked worker at ~``interval_s / 2`` cadence.

        A worker whose last-seen is older than
        ``miss_threshold * interval_s`` is flipped unhealthy, with
        ``on_unhealthy`` fired exactly once per episode (recovery
        re-arms the trigger).

        The loop exits promptly on ``stop()``; callers typically shield
        the task and await it during ``shutdown()``.
        """
        check_interval = self._interval_s / 2
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=check_interval
                )
            except TimeoutError:
                pass
            else:
                break
            now = self._now()
            stale_after = self._interval_s * self._miss_threshold
            for wid, last in list(self._last_seen.items()):
                if wid in self._unhealthy:
                    continue
                if now - last > stale_after:
                    self._unhealthy.add(wid)
                    await _maybe_await(self._on_unhealthy, wid)


async def watch_worker(
    *,
    pid: int,
    limits: ResourceLimitsSnapshot,
    interval_s: float,
    on_violation: Callable[[Violation], Awaitable[None]],
) -> None:
    """Poll ``check_limits`` at ``interval_s`` cadence until termination.

    The loop exits under any of:
      * ``check_limits`` returns a :class:`Violation` — forwarded to
        ``on_violation`` then the coroutine returns.
      * ``check_limits`` raises :class:`psutil.NoSuchProcess` — the worker
        died on its own; the callback is *not* invoked and the monitor
        exits quietly so the kernel's regular crash path owns the audit.
      * The task is cancelled — standard ``asyncio.CancelledError``
        propagation; callers supply ``asyncio.shield`` / timeouts as they
        prefer.
    """
    while True:
        try:
            result = check_limits(pid=pid, limits=limits)
        except psutil.NoSuchProcess:
            return
        if result is not None:
            await on_violation(result)
            return
        await asyncio.sleep(interval_s)
