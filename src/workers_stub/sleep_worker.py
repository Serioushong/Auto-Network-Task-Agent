"""T066 — Sleep worker stub (US4 cancel / US5 hard-terminate scenarios).

Declares the ``sleep.wait`` capability at ``NORMAL`` risk. On dispatch
it sleeps for ``payload.seconds`` seconds in small increments,
checking its stdin for an ``abort`` frame between naps so the cancel
flow can reach it without requiring POSIX-style signals (which on
Windows / PowerShell are awkward to simulate).

Two environment knobs let the integration tests exercise the
signal-escalation matrix:

* ``SLEEP_WORKER_IGNORE_ABORT=1`` — the worker ignores ``abort`` frames
  *and* any SIGTERM / SIGBREAK; it only exits on hard ``kill()``. Drives
  the ``hard_terminated`` / US5 Scenario 3 branch.
* Absent / any other value — the worker responds to the first ``abort``
  frame by emitting ``result(outcome=failed, failureReason=hard_terminated)``
  and exits cleanly. Drives the "obedient worker" branch.

The sleep loop always yields back to stdin at 50 ms granularity so
even a 10 s sleep is cancellable well within the ≤ 5 s budget of
FR-013.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import UTC, datetime

from _heartbeat import start_heartbeat_thread  # noqa: E402 — sibling module (script mode)

from orchestrator_kernel.contracts.budget import Budget
from orchestrator_kernel.contracts.worker import (
    Capability,
    ResourceLimits,
    WorkerRegistration,
)
from orchestrator_kernel.contracts.worker_protocol import (
    RegisterFrame,
    ResultFrame,
    StartedFrame,
)

WORKER_ID_PREFIX = "sleep-worker"
CAPABILITY_NAME = "sleep.wait"

_SLEEP_BUDGET = Budget(
    wall_clock_ms=60_000, max_tool_calls=1, max_tokens=1_000
)
_IGNORE_ABORT_ENV = "SLEEP_WORKER_IGNORE_ABORT"
_DISABLE_HEARTBEAT_ENV = "SLEEP_WORKER_DISABLE_HEARTBEAT"
_POLL_INTERVAL_S = 0.05

_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("sleep-worker tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[sleep-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="NORMAL",
        budget=_SLEEP_BUDGET,
        description="Cooperative sleep with cancel-friendly polling.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(
            memory_mb=128, cpu_pct=10, wall_clock_ms=120_000
        ),
    )
    return RegisterFrame(kind="register", registration=registration)


class _DispatchState:
    """Tiny in-process flag set; stdin reader toggles it from a bg thread."""

    def __init__(self) -> None:
        self.abort_task_id: str | None = None
        self.shutdown = False


def _stdin_reader(state: _DispatchState, ignore_abort: bool) -> None:
    """Background thread that pumps stdin frames into the state flags."""
    stdin = sys.stdin.buffer
    while not state.shutdown:
        try:
            line = stdin.readline()
        except (KeyboardInterrupt, BrokenPipeError):
            state.shutdown = True
            return
        if not line:
            state.shutdown = True
            return
        try:
            obj = json.loads(line.decode("utf-8").rstrip("\r\n"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log_stderr(f"malformed frame skipped: {exc!r}")
            continue
        if not isinstance(obj, dict):
            _log_stderr(f"frame is not a JSON object; skipping: {obj!r}")
            continue
        kind = obj.get("kind")
        if kind == "abort":
            if ignore_abort:
                _log_stderr(
                    f"abort received but ignored ({_IGNORE_ABORT_ENV} set)"
                )
                continue
            task_id = obj.get("taskId")
            if isinstance(task_id, str):
                state.abort_task_id = task_id
        elif kind == "shutdown":
            state.shutdown = True
            return
        elif kind == "dispatch":
            _handle_dispatch(obj, state)


def _handle_dispatch(obj: dict[str, object], state: _DispatchState) -> None:
    task_id = obj.get("taskId")
    payload = obj.get("payload") or {}
    if not isinstance(task_id, str):
        _log_stderr(f"dispatch missing taskId; ignoring frame={obj!r}")
        return
    if not isinstance(payload, dict):
        payload = {}
    seconds_raw = payload.get("seconds", 1)
    try:
        seconds = float(seconds_raw)
    except (TypeError, ValueError):
        seconds = 1.0

    started = StartedFrame(kind="started", taskId=task_id, startedAt=_now_utc())
    _emit(started.model_dump_json(exclude_none=True))

    deadline = time.monotonic() + seconds
    aborted = False
    while time.monotonic() < deadline:
        if state.abort_task_id == task_id:
            aborted = True
            break
        if state.shutdown:
            aborted = True
            break
        time.sleep(_POLL_INTERVAL_S)

    if aborted:
        result = ResultFrame(
            kind="result",
            taskId=task_id,
            outcome="failed",
            failureReason="hard_terminated",
            finishedAt=_now_utc(),
        )
    else:
        result = ResultFrame(
            kind="result",
            taskId=task_id,
            outcome="succeeded",
            output={"text": f"slept {seconds:.2f}s"},
            finishedAt=_now_utc(),
        )
    _emit(result.model_dump_json(exclude_none=True))
    state.abort_task_id = None


def main() -> int:
    try:
        register = _build_register_frame()
        _emit(register.model_dump_json(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        _log_stderr(f"failed to emit register frame: {exc!r}")
        return 2

    start_heartbeat_thread(
        worker_id=register.registration.workerId,
        stdout_lock=_stdout_lock,
        env_disable_key=_DISABLE_HEARTBEAT_ENV,
    )

    ignore_abort = bool(os.environ.get(_IGNORE_ABORT_ENV))
    state = _DispatchState()
    reader = threading.Thread(
        target=_stdin_reader, args=(state, ignore_abort), daemon=True
    )
    reader.start()

    while not state.shutdown:
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
