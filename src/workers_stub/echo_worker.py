"""T043 — Echo worker stub (US1 MVP).

Standalone Python script launched by ``WorkerSupervisor.spawn`` as its own
subprocess. It exchanges JSON-Lines frames with the kernel over stdin/stdout
per ``contracts/worker-protocol.schema.json``.

Wire behaviour:

1. On startup, emit one ``register`` frame declaring the capability
   ``echo.say`` at ``NORMAL`` risk with the conservative default budget.
2. For every ``dispatch`` frame received on stdin, respond within ~50 ms
   with ``started`` followed by ``result(succeeded, output.text=payload.text)``.
3. A ``shutdown`` frame causes a clean ``sys.exit(0)``.
4. Any frame that fails JSON / schema validation is logged to stderr and
   skipped — the parent process MUST survive malformed input (per
   ``tests/integration/test_worker_stdio_roundtrip.py::test_worker_rejects_malformed_dispatch_gracefully``).

Design notes:

- Uses the binary stdio streams (``sys.stdin.buffer`` / ``sys.stdout.buffer``)
  so we can enforce exact byte-level framing (``b"\\n"`` terminators, no
  trailing whitespace) regardless of platform line-ending defaults.
- Imports the same pydantic frame models the kernel uses so that, by
  construction, every emitted frame already passes ``WorkerFrame``
  validation — the contract test parses our stdout with the exact same
  codec the kernel runs.
- Does NOT open any files, sockets, threads, or subprocesses. A worker
  stub that keeps the invariants simple lets the crash-isolation test
  (T070) later prove real isolation by adding *explicit* misbehaviour.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from orchestrator_kernel.contracts.budget import DEFAULT_BUDGET
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

WORKER_ID_PREFIX = "echo-worker"
CAPABILITY_NAME = "echo.say"


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _emit(frame_json: str) -> None:
    """Write one frame + LF to stdout.buffer and flush. One line, UTF-8."""
    if "\n" in frame_json:
        raise RuntimeError("echo-worker tried to emit embedded newline")
    sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _log_stderr(msg: str) -> None:
    try:
        sys.stderr.write(f"[echo-worker] {msg}\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — best-effort diagnostics, never crash
        pass


def _build_register_frame() -> RegisterFrame:
    capability = Capability(
        name=CAPABILITY_NAME,
        riskLevel="NORMAL",
        budget=DEFAULT_BUDGET,
        description="Echo a payload.text string back as output.text.",
    )
    registration = WorkerRegistration(
        workerId=f"{WORKER_ID_PREFIX}-{os.getpid()}",
        pid=os.getpid(),
        capabilities=[capability],
        resourceLimits=ResourceLimits(memory_mb=128, cpu_pct=50, wall_clock_ms=60_000),
    )
    return RegisterFrame(kind="register", registration=registration)


def _handle_dispatch(obj: dict[str, object]) -> None:
    """Emit started + result frames for one dispatch payload.

    Only the fields strictly required to satisfy
    ``contracts/worker-protocol.schema.json`` are read from ``obj``; the
    rest (``traceId``, ``capability``, ``budget``, ``deadline``) are
    trusted because the kernel already validated them before writing to
    our stdin.
    """
    task_id = obj.get("taskId")
    payload = obj.get("payload") or {}
    if not isinstance(task_id, str):
        _log_stderr(f"dispatch missing taskId; ignoring frame={obj!r}")
        return
    if not isinstance(payload, dict):
        payload = {}

    text = payload.get("text")
    echoed: str = text if isinstance(text, str) else ""

    started = StartedFrame(kind="started", taskId=task_id, startedAt=_now_utc())
    _emit(started.model_dump_json(exclude_none=True))

    result = ResultFrame(
        kind="result",
        taskId=task_id,
        outcome="succeeded",
        output={"text": echoed},
        finishedAt=_now_utc(),
    )
    _emit(result.model_dump_json(exclude_none=True))


def main() -> int:
    """Run the stdio read loop. Returns a POSIX-style exit code."""
    try:
        register = _build_register_frame()
        _emit(register.model_dump_json(exclude_none=True))
    except Exception as exc:  # noqa: BLE001 — startup must never explode silently
        _log_stderr(f"failed to emit register frame: {exc!r}")
        return 2

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
            except Exception as exc:  # noqa: BLE001 — keep reader alive
                _log_stderr(f"dispatch handler error: {exc!r}")
        elif kind == "abort":
            continue
        elif kind == "shutdown":
            return 0
        else:
            _log_stderr(f"ignored frame kind={kind!r}")


if __name__ == "__main__":
    sys.exit(main())
