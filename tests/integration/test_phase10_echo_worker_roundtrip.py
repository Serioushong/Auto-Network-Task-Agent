from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator_kernel.worker_supervisor.protocol import decode_frame, encode_frame


@pytest.mark.integration
async def test_phase10_echo_worker_register_and_roundtrip() -> None:
    script = Path(__file__).resolve().parents[2] / "src" / "workers_stub" / "phase10_echo_worker.py"
    proc = await asyncio.create_subprocess_exec(
        str(Path.cwd() / ".venv" / "Scripts" / "python.exe") if (Path.cwd() / ".venv" / "Scripts" / "python.exe").exists() else "python",
        str(script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    assert proc.stdin is not None
    try:
        register_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        register = decode_frame(register_line)
        assert register.kind == "register"
        task_id = "01KQ1TASK00000000000000000"
        dispatch = {
            "kind": "dispatch",
            "taskId": task_id,
            "traceId": "01KQ1TRACE0000000000000000",
            "capability": "echo.say",
            "payload": {"text": "hello"},
            "budget": {
                "wallClockMs": 60000,
                "maxToolCalls": 1,
                "maxTokens": 1000,
            },
            "deadline": "2026-04-25T12:00:05Z",
        }
        proc.stdin.write(encode_frame(dispatch))
        await proc.stdin.drain()
        seen_started = False
        seen_result = False
        while not (seen_started and seen_result):
            frame = decode_frame(await asyncio.wait_for(proc.stdout.readline(), timeout=5))
            if frame.kind == "started":
                seen_started = True
                continue
            if frame.kind == "result":
                seen_result = True
                assert frame.output["text"] == "hello"
                continue
            if frame.kind == "heartbeat":
                continue
            pytest.fail(f"unexpected frame kind={frame.kind!r}")
        proc.stdin.write(encode_frame({"kind": "shutdown"}))
        await proc.stdin.drain()
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
