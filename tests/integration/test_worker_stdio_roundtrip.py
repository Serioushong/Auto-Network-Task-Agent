"""T038 — Phase 3 US1 RED: byte-level Kernel<->Worker stdio round-trip.

Boots the echo-worker stub as a real subprocess and exchanges raw JSON-lines
frames via its stdin/stdout, asserting byte-level conformance with
`contracts/worker-protocol.schema.json`:

1. The first line emitted on stdout is a well-formed `register` frame that
   validates against `WorkerRegistration`.
2. After the kernel writes a `dispatch` frame on stdin, the worker emits
   `started` then `result(succeeded)` frames in order.
3. Every line is a single JSON object terminated by `\\n`, with no trailing
   whitespace and no embedded newlines inside values (FR-024 wire format).
4. Each emitted frame `WorkerFrame.validate_python(...)` parses without
   error.

This is intentionally lower level than `test_p1_basic_loop` — it bypasses
the dispatcher and goes straight to the protocol. Failure today: the
`echo_worker.py` script does not exist yet (T043), so `subprocess.Popen`
raises `FileNotFoundError`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestrator_kernel.contracts.worker_protocol import WorkerFrame


def _read_one_frame(proc: subprocess.Popen[bytes], timeout_s: float = 2.0) -> dict:
    """Read one JSON-line frame from `proc.stdout`, enforcing the wire format.

    Heartbeat frames (FR-009 / T076) are transparently skipped so the
    register/dispatch/started/result sequence asserts stay readable. The
    wire-format checks (LF terminator, no embedded newlines, trim
    whitespace) are still enforced against every intermediate line,
    including heartbeats.
    """
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"no frame from worker within {timeout_s}s (stderr: "
                f"{proc.stderr.read() if proc.stderr else b''!r})"
            )
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.01)
            continue
        assert line.endswith(b"\n"), f"frame not LF-terminated: {line!r}"
        body = line[:-1]
        assert b"\n" not in body, f"frame contains embedded newline: {line!r}"
        assert body == body.strip(), f"frame has surrounding whitespace: {line!r}"
        obj = json.loads(body.decode("utf-8"))
        if isinstance(obj, dict) and obj.get("kind") == "heartbeat":
            continue
        return obj


@pytest.mark.integration
def test_echo_worker_register_frame_is_wire_clean(
    echo_worker_script: Path,
) -> None:
    """First line from the worker MUST be a valid `register` frame, byte-exact."""
    assert echo_worker_script.exists(), (
        f"echo-worker script {echo_worker_script} missing (T043)"
    )

    proc = subprocess.Popen(
        [sys.executable, str(echo_worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        frame = _read_one_frame(proc)
        parsed = WorkerFrame.validate_python(frame)
        assert parsed.kind == "register"
        assert parsed.registration.capabilities
        assert any(
            c.name == "echo.say" for c in parsed.registration.capabilities
        )
    finally:
        proc.kill()
        proc.wait(timeout=1)


@pytest.mark.integration
def test_echo_worker_dispatch_started_result_sequence(
    echo_worker_script: Path,
) -> None:
    """dispatch -> started -> result(succeeded); sequence and byte format exact."""
    assert echo_worker_script.exists(), (
        f"echo-worker script {echo_worker_script} missing (T043)"
    )

    proc = subprocess.Popen(
        [sys.executable, str(echo_worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        register = _read_one_frame(proc)
        assert register["kind"] == "register"

        dispatch = {
            "kind": "dispatch",
            "taskId": "01J9LEAF0000000001",
            "traceId": "01J9TRACE000000001",
            "capability": "echo.say",
            "payload": {"text": "hello"},
            "budget": {
                "wall_clock_ms": 60_000,
                "max_tool_calls": 10,
                "max_tokens": 20_000,
            },
            "deadline": "2099-01-01T00:00:00Z",
        }
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(dispatch) + "\n").encode("utf-8"))
        proc.stdin.flush()

        started = _read_one_frame(proc)
        assert started["kind"] == "started"
        assert started["taskId"] == "01J9LEAF0000000001"

        result = _read_one_frame(proc)
        assert result["kind"] == "result"
        assert result["taskId"] == "01J9LEAF0000000001"
        assert result["outcome"] == "succeeded"

        for raw in (register, started, result):
            WorkerFrame.validate_python(raw)
    finally:
        proc.kill()
        proc.wait(timeout=1)


@pytest.mark.integration
def test_worker_rejects_malformed_dispatch_gracefully(
    echo_worker_script: Path,
) -> None:
    """Worker receives garbage JSON; MUST NOT crash the parent process."""
    assert echo_worker_script.exists(), (
        f"echo-worker script {echo_worker_script} missing (T043)"
    )

    proc = subprocess.Popen(
        [sys.executable, str(echo_worker_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        _ = _read_one_frame(proc)

        assert proc.stdin is not None
        proc.stdin.write(b"{not json\n")
        proc.stdin.flush()

        time.sleep(0.2)
        # Harness only cares that the parent survived — exit status may or
        # may not be non-zero depending on the stub's error policy. What
        # matters is that we can still send a shutdown and the stub reaps.
        proc.stdin.write(b'{"kind":"shutdown"}\n')
        proc.stdin.flush()
        proc.wait(timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)
