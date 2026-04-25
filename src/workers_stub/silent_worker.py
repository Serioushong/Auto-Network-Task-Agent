"""Phase N.1 support — Silent worker stub (FR-009 / T076).

Registers the ``silent.noop`` capability and then **deliberately never
emits a heartbeat**. Sits quietly reading stdin until ``shutdown`` /
pipe close, so the kernel's :class:`HeartbeatTracker` has a canonical
fixture for proving that a live-but-silent Worker gets flipped to
``healthy=False`` after ~3 × interval_s.

Wire behaviour:

1. Emit one ``register`` frame declaring ``silent.noop`` at
   ``NORMAL`` risk.
2. Do **not** start any heartbeat emitter.
3. On dispatch, emit ``started`` → 20 ms later ``result(succeeded)``
   (defensive; integration tests never actually get to dispatch because
   the dispatcher's health filter removes us first).
4. Exit on ``shutdown`` or closed stdin.

The worker is deliberately minimal — no env knobs, no optional
behaviour — so the integration test surface stays tiny.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime

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

WORKER_ID_PREFIX = "silent-worker"
CAPABILITY_NAME = "silent.noop"
_BUDGET = Budget(wall_clock_ms=5_000, max_tool_calls=1, max_tokens=1_000)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("silent-worker tried to emit embedded newline")
    sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[silent-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="NORMAL",
        budget=_BUDGET,
        description="Never heartbeats; fixture for unhealthy-flip tests.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(memory_mb=64, cpu_pct=5, wall_clock_ms=5_000),
    )
    return RegisterFrame(kind="register", registration=registration)


def _handle_dispatch(obj: dict[str, object]) -> None:
    task_id = obj.get("taskId")
    if not isinstance(task_id, str):
        return
    _emit(
        StartedFrame(
            kind="started", taskId=task_id, startedAt=_now_utc()
        ).model_dump_json(exclude_none=True)
    )
    time.sleep(0.02)
    _emit(
        ResultFrame(
            kind="result",
            taskId=task_id,
            outcome="succeeded",
            output={"text": "noop"},
            finishedAt=_now_utc(),
        ).model_dump_json(exclude_none=True)
    )


def main() -> int:
    try:
        register = _build_register_frame()
        _emit(register.model_dump_json(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        _log_stderr(f"failed to emit register frame: {exc!r}")
        return 2

    # NOTE: deliberately NO heartbeat thread.
    stdin = sys.stdin.buffer
    while True:
        try:
            line = stdin.readline()
        except (KeyboardInterrupt, BrokenPipeError):
            return 0
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
        if kind == "dispatch":
            _handle_dispatch(obj)


if __name__ == "__main__":
    sys.exit(main())
