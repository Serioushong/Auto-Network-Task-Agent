"""T075 — Crash worker stub (US5 Scenario 1 / SC-005).

Declares two capabilities that intentionally kill the worker process
mid-dispatch so the integration test can verify the kernel's main loop
survives (``test_p5_crash_isolation.py``):

* ``crash.raise`` — emit ``started`` then ``raise RuntimeError`` +
  ``sys.exit(1)``. Triggers the ``worker_crashed`` failure path because
  the worker's stdout closes before a ``result`` frame arrives.
* ``crash.oom`` — emit ``started`` then allocate ~256 MiB bytearrays in a
  tight loop. Intended as a companion for T073 resource-monitor tests
  once the kernel side lands; in this file it is **bounded** (hard caps
  at 16 chunks of 16 MiB = 256 MiB then exits 137) so a runaway test
  never OOMs the CI host.

Both capabilities register at ``NORMAL`` risk because US5 is about
runtime isolation, not policy. Only one task is handled per dispatch;
after crashing the worker process exits and the kernel observes the
channel close.
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
    StartedFrame,
)

WORKER_ID_PREFIX = "crash-worker"
_CRASH_BUDGET = Budget(
    wall_clock_ms=10_000, max_tool_calls=1, max_tokens=1_000
)
_OOM_CHUNK_MB = 16
_OOM_MAX_CHUNKS = 16  # 256 MiB hard ceiling so the host never dies with us.
_DISABLE_HEARTBEAT_ENV = "CRASH_WORKER_DISABLE_HEARTBEAT"

_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("crash-worker tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[crash-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capabilities = [
        Capability(
            name="crash.raise",
            riskLevel="NORMAL",
            budget=_CRASH_BUDGET,
            description="Deliberately raises mid-dispatch for US5 tests.",
        ),
        Capability(
            name="crash.oom",
            riskLevel="NORMAL",
            budget=_CRASH_BUDGET,
            description="Bounded memory-spike for resource-monitor tests.",
        ),
    ]
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=capabilities,
        resourceLimits=ResourceLimits(
            memory_mb=512, cpu_pct=50, wall_clock_ms=10_000
        ),
    )
    return RegisterFrame(kind="register", registration=registration)


def _emit_started(task_id: str) -> None:
    started = StartedFrame(kind="started", taskId=task_id, startedAt=_now_utc())
    _emit(started.model_dump_json(exclude_none=True))


def _handle_crash_raise(task_id: str) -> None:
    _emit_started(task_id)
    _log_stderr("deliberate crash.raise for US5 test")
    raise RuntimeError("deliberate crash.raise for US5 test")


def _handle_crash_oom(task_id: str) -> None:
    _emit_started(task_id)
    hogs: list[bytearray] = []
    for _ in range(_OOM_MAX_CHUNKS):
        hogs.append(bytearray(_OOM_CHUNK_MB * 1024 * 1024))
    # Reached the ceiling without the kernel terminating us; exit loudly so
    # any misconfigured test sees the anomaly rather than a silent hang.
    _log_stderr(
        f"crash.oom reached self-imposed ceiling "
        f"{_OOM_CHUNK_MB * _OOM_MAX_CHUNKS} MiB without being killed"
    )
    sys.exit(137)


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
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log_stderr(f"malformed frame skipped: {exc!r}")
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("kind")
        if kind == "shutdown":
            return 0
        if kind != "dispatch":
            continue
        task_id = obj.get("taskId")
        capability = obj.get("capability")
        if not isinstance(task_id, str):
            _log_stderr(f"dispatch missing taskId: {obj!r}")
            continue
        if capability == "crash.raise":
            _handle_crash_raise(task_id)
            return 1  # unreachable; _handle_crash_raise raises first.
        if capability == "crash.oom":
            _handle_crash_oom(task_id)
            return 1
        _log_stderr(f"unknown capability={capability!r}; ignoring")


if __name__ == "__main__":
    sys.exit(main())
