"""T072/T077 support — Budget-burner worker stub.

Registers a single ``budget.burn`` capability whose capability-level
``Budget.wall_clock_ms`` is **300 ms**, but whose dispatch handler
unconditionally sleeps 10 s while ignoring every abort frame. This is
the canonical fixture for ``test_budget_exceeded.py``:

* On dispatch → emit ``started`` → sleep 10 s in small slices (so an
  eventual hard-kill is still responsive) → if somehow we finish, emit
  a ``result(succeeded)`` (defensive; the test should never reach it).

The worker ignores ``AbortFrame``s; it only dies from
``Process.terminate()`` / ``Process.kill()``. That keeps the test
focused on the **budget** branch — we want to see the kernel issue a
``task_failed(budget_exceeded, dim=wall)`` because the *wall clock*
elapsed, not because the worker voluntarily cooperated with abort.
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

WORKER_ID_PREFIX = "budget-worker"
CAPABILITY_NAME = "budget.burn"
_BURN_BUDGET = Budget(
    wall_clock_ms=300, max_tool_calls=1, max_tokens=1_000
)
_BURN_DURATION_S = 10.0
_SLICE_S = 0.1
_DISABLE_HEARTBEAT_ENV = "BUDGET_WORKER_DISABLE_HEARTBEAT"

_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("budget-worker tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[budget-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="NORMAL",
        budget=_BURN_BUDGET,
        description="Intentionally exceeds wall_clock_ms for T072 tests.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(
            memory_mb=128, cpu_pct=10, wall_clock_ms=60_000
        ),
    )
    return RegisterFrame(kind="register", registration=registration)


def _handle_dispatch(task_id: str) -> None:
    _emit(
        StartedFrame(
            kind="started", taskId=task_id, startedAt=_now_utc()
        ).model_dump_json(exclude_none=True)
    )
    deadline = time.monotonic() + _BURN_DURATION_S
    while time.monotonic() < deadline:
        time.sleep(_SLICE_S)
    # Defensive: if we ever complete naturally, report a success frame. The
    # integration test should kill us long before this point.
    result = ResultFrame(
        kind="result",
        taskId=task_id,
        outcome="succeeded",
        output={"text": "burn completed (unexpected in budget tests)"},
        finishedAt=_now_utc(),
    )
    _emit(result.model_dump_json(exclude_none=True))


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

    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0
        try:
            obj = json.loads(line.decode("utf-8").rstrip("\r\n"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("kind")
        if kind == "shutdown":
            return 0
        if kind == "abort":
            _log_stderr("abort ignored; budget-worker only dies from kill()")
            continue
        if kind == "dispatch":
            task_id = obj.get("taskId")
            if isinstance(task_id, str):
                _handle_dispatch(task_id)


if __name__ == "__main__":
    sys.exit(main())
