"""T058 — Danger worker stub (US3 HIGH_RISK capability).

Declares the ``file.delete`` capability at ``HIGH_RISK`` so the
kernel's approval gate must park every dispatch in
``pending_approval`` (INV-3). The worker itself never touches disk:
on dispatch it echoes back ``output.text = "would-delete <path>"`` —
sufficient to prove the control-plane semantics without introducing
actual filesystem effects to integration tests.

Wire behaviour is identical to ``echo_worker.py``: one register frame,
then a streaming ``dispatch -> started -> result`` loop, closing on
``shutdown``. Malformed JSON is logged to stderr and skipped.
"""

from __future__ import annotations

import json
import os
import sys
import threading
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

WORKER_ID_PREFIX = "danger-worker"
CAPABILITY_NAME = "file.delete"
_DISABLE_HEARTBEAT_ENV = "DANGER_WORKER_DISABLE_HEARTBEAT"

_DANGER_BUDGET = Budget(wall_clock_ms=5_000, max_tool_calls=1, max_tokens=1_000)

_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("danger-worker tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[danger-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="HIGH_RISK",
        budget=_DANGER_BUDGET,
        description="Simulated file deletion; never touches the filesystem.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(memory_mb=128, cpu_pct=25, wall_clock_ms=10_000),
    )
    return RegisterFrame(kind="register", registration=registration)


def _handle_dispatch(obj: dict[str, object]) -> None:
    task_id = obj.get("taskId")
    payload = obj.get("payload") or {}
    if not isinstance(task_id, str):
        _log_stderr(f"dispatch missing taskId; ignoring frame={obj!r}")
        return
    if not isinstance(payload, dict):
        payload = {}

    path = payload.get("path") or payload.get("text") or ""
    blurb: str = path if isinstance(path, str) else ""

    started = StartedFrame(kind="started", taskId=task_id, startedAt=_now_utc())
    _emit(started.model_dump_json(exclude_none=True))

    result = ResultFrame(
        kind="result",
        taskId=task_id,
        outcome="succeeded",
        output={"text": f"would-delete {blurb}".rstrip()},
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
        try:
            line = stdin.readline()
        except (KeyboardInterrupt, BrokenPipeError):
            return 0

        if not line:
            return 0

        try:
            obj = json.loads(line.decode("utf-8").rstrip("\r\n"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log_stderr(f"malformed frame skipped: {exc!r}")
            continue

        if not isinstance(obj, dict):
            _log_stderr(f"frame is not a JSON object; skipping: {obj!r}")
            continue

        kind = obj.get("kind")
        if kind == "dispatch":
            try:
                _handle_dispatch(obj)
            except Exception as exc:  # noqa: BLE001
                _log_stderr(f"dispatch handler error: {exc!r}")
        elif kind == "abort":
            continue
        elif kind == "shutdown":
            return 0
        else:
            _log_stderr(f"ignored frame kind={kind!r}")


if __name__ == "__main__":
    sys.exit(main())
