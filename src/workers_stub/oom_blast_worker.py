"""OOM blast worker stub — real Windows Job Object enforcement witness.

Unlike ``crash_worker.crash.oom`` (which self-caps at 256 MiB to protect
CI hosts), this worker declares a tight ``memory_mb=64`` sandbox and
then tries to allocate up to ``_SAFETY_CEILING_MB`` in tight 16 MiB
increments **with no cooperative self-limit**. The expectation is:

  1. Kernel calls ``supervisor.bind_sandbox`` after reading our register
     frame, wrapping this PID in a Job Object with
     ``JOB_OBJECT_LIMIT_PROCESS_MEMORY=64 MiB`` +
     ``KILL_ON_JOB_CLOSE`` + ``DIE_ON_UNHANDLED_EXCEPTION``.
  2. On dispatch of ``oom.blast`` we emit ``started`` and then start
     allocating + **touching** (writing every 4 KiB page) bytearrays so
     the OS actually commits the pages — Windows does not charge an
     uncommitted ``bytearray`` against a Job Object's working-set quota.
  3. Somewhere between 64 MiB and ``_SAFETY_CEILING_MB`` the Windows
     kernel issues ``TerminateProcess`` and the worker dies with a
     non-zero exit code, stdout EOFs, and the kernel surfaces
     ``failureReason=worker_crashed`` (or ``sandbox_limit`` if the
     psutil monitor fires first).

``_SAFETY_CEILING_MB`` exists strictly as a belt-and-braces: if a bug
ever causes Job Object binding to silently fail, the worker still exits
on its own before consuming enough RAM to matter. On a healthy Windows
kernel we MUST die long before hitting the ceiling.

The capability is gated behind ``risk_level="NORMAL"`` because the
memory blast is runtime-level, not policy-level. Heartbeat is emitted
on a separate thread so the kernel-side HeartbeatTracker stays happy
while we're allocating.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from datetime import UTC, datetime

from _heartbeat import start_heartbeat_thread  # noqa: E402

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

WORKER_ID_PREFIX = "oom-blast-worker"
_DECLARED_MEMORY_MB = 64
_CHUNK_MB = 16
_SAFETY_CEILING_MB = 512
_BUDGET = Budget(wall_clock_ms=30_000, max_tool_calls=1, max_tokens=1_000)
_DISABLE_HEARTBEAT_ENV = "OOM_BLAST_WORKER_DISABLE_HEARTBEAT"

_stdout_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    if "\n" in frame_json:
        raise RuntimeError("oom-blast tried to emit embedded newline")
    with _stdout_lock:
        sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[oom-blast] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def _build_register_frame() -> RegisterFrame:
    capabilities = [
        Capability(
            name="oom.blast",
            riskLevel="NORMAL",
            budget=_BUDGET,
            description="Unbounded memory blast to witness Job Object kill.",
        ),
    ]
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=capabilities,
        resourceLimits=ResourceLimits(
            memory_mb=_DECLARED_MEMORY_MB,
            cpu_pct=50,
            wall_clock_ms=30_000,
        ),
    )
    return RegisterFrame(kind="register", registration=registration)


def _emit_started(task_id: str) -> None:
    started = StartedFrame(
        kind="started", taskId=task_id, startedAt=_now_utc()
    )
    _emit(started.model_dump_json(exclude_none=True))


def _handle_blast(task_id: str) -> None:
    """Allocate + touch pages until the Job Object kills us.

    The ``ctypes.memset`` call forces the Windows memory manager to
    commit each page — a pure ``bytearray(size)`` only reserves address
    space on some allocator paths and would not count against the Job
    Object's ``ProcessMemoryLimit``. Touching every 4 KiB guarantees the
    quota actually gets charged.
    """
    _emit_started(task_id)
    _log_stderr(
        f"starting blast: chunk={_CHUNK_MB} MiB, ceiling={_SAFETY_CEILING_MB} MiB"
    )
    hogs: list[bytearray] = []
    chunks_needed = _SAFETY_CEILING_MB // _CHUNK_MB
    for idx in range(chunks_needed):
        buf = bytearray(_CHUNK_MB * 1024 * 1024)
        ctypes.memset(
            (ctypes.c_char * len(buf)).from_buffer(buf),
            0xAB,
            len(buf),
        )
        hogs.append(buf)
        total_mb = (idx + 1) * _CHUNK_MB
        _log_stderr(f"chunk {idx + 1}/{chunks_needed} allocated; total={total_mb} MiB")
    _log_stderr(
        f"reached safety ceiling {_SAFETY_CEILING_MB} MiB without being killed - "
        f"Job Object binding likely failed"
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
        if capability == "oom.blast":
            _handle_blast(task_id)
            return 1  # unreachable under Job Object
        _log_stderr(f"unknown capability={capability!r}; ignoring")


if __name__ == "__main__":
    sys.exit(main())
