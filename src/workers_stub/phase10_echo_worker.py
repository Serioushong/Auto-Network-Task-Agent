"""Phase 10 sub-agent worker stub.

This worker is intentionally tiny: a single-capability, independent
process worker used to prove the main-agent -> sub-agent loop in Phase 10.
It mirrors the existing echo worker semantics but lives under a distinct
module so Batch D can reference a Phase 10-specific entrypoint.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime

from _heartbeat import start_heartbeat_thread
from orchestrator_kernel.contracts.budget import DEFAULT_BUDGET
from orchestrator_kernel.contracts.worker import Capability, ResourceLimits, WorkerRegistration
from orchestrator_kernel.contracts.worker_protocol import RegisterFrame, ResultFrame, StartedFrame

WORKER_ID_PREFIX = "phase10-echo-worker"
CAPABILITY_NAME = "echo.say"
_DISABLE_HEARTBEAT_ENV = "PHASE10_ECHO_WORKER_DISABLE_HEARTBEAT"
_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("phase10 echo worker tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="NORMAL",
        budget=DEFAULT_BUDGET,
        description="Phase 10 echo worker.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(memory_mb=128, cpu_pct=50, wall_clock_ms=60_000),
    )
    return RegisterFrame(kind="register", registration=registration)


def _handle_dispatch(obj: dict[str, object]) -> None:
    task_id = obj.get("taskId")
    payload = obj.get("payload") or {}
    if not isinstance(task_id, str):
        return
    if not isinstance(payload, dict):
        payload = {}
    text = payload.get("text")
    echoed = text if isinstance(text, str) else ""
    _emit(StartedFrame(kind="started", taskId=task_id, startedAt=_now_utc()).model_dump_json(exclude_none=True))
    _emit(
        ResultFrame(
            kind="result",
            taskId=task_id,
            outcome="succeeded",
            output={"text": echoed},
            finishedAt=_now_utc(),
        ).model_dump_json(exclude_none=True)
    )


def main() -> int:
    try:
        register = _build_register_frame()
        _emit(register.model_dump_json(exclude_none=True))
    except Exception:
        return 2

    start_heartbeat_thread(
        worker_id=register.registration.workerId,
        stdout_lock=_stdout_lock,
        env_disable_key=_DISABLE_HEARTBEAT_ENV,
    )

    while True:
        line = sys.stdin.buffer.readline()
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
    raise SystemExit(main())
