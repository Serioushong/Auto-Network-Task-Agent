from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator_kernel.worker_supervisor.protocol import decode_frame, encode_frame


@pytest.mark.integration
async def test_phase10_echo_worker_handles_abort_and_shutdown() -> None:
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
        await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        proc.stdin.write(encode_frame({"kind": "abort", "taskId": "01KQ1TASK00000000000000000", "reason": "user_cancel"}))
        await proc.stdin.drain()
        proc.stdin.write(encode_frame({"kind": "shutdown"}))
        await proc.stdin.drain()
        await asyncio.wait_for(proc.wait(), timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
