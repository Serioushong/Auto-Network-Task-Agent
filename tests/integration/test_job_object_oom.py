"""Evidence #14 — real Windows Job Object OOM enforcement (no mocks).

Drives ``WorkerSupervisor`` + ``oom_blast_worker`` end-to-end to witness
that the ``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` binding **actually terminates**
the subprocess in the Windows kernel — not via our ``psutil`` soft
monitor, not via the cooperative abort path, not via the self-declared
safety ceiling.

Discriminator between "Job Object worked" vs "Job Object silently
failed / not bound":

  * ``oom_blast_worker`` registers ``memory_mb=64`` and then tries to
    commit memory up to ``_SAFETY_CEILING_MB=512`` (logged per chunk to
    stderr).
  * If the Job Object is active, the Windows kernel issues
    ``TerminateProcess`` somewhere around chunk 4-5 (≈64-80 MiB once
    reserved-but-touched pages exceed the cap). stderr ends
    mid-allocation with no "reached safety ceiling" line.
  * If the Job Object is NOT active, the worker happily allocates all
    512 MiB on any modern dev machine (~1-2 s) and exits 137 with the
    distinctive "reached safety ceiling" log line → **test fails loudly**.

The test is POSIX-skipped because ``bind_sandbox`` is intentionally a
no-op there (POSIX ``RLIMIT_AS`` must be set at fork time via
``preexec_fn`` in ``spawn``; the late-bind ctypes path is Windows-only).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator_kernel.contracts.budget import Budget
from orchestrator_kernel.contracts.worker import ResourceLimits
from orchestrator_kernel.contracts.worker_protocol import DispatchFrame
from orchestrator_kernel.worker_supervisor.protocol import write_frame
from orchestrator_kernel.worker_supervisor.supervisor import WorkerSupervisor

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows Job Object specific — POSIX uses preexec_fn + RLIMIT_AS",
)


@pytest.fixture
def oom_blast_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "oom_blast_worker.py"
    )


async def _read_register_frame(worker: object) -> dict:
    """Read one line of stdout and decode the register envelope."""
    stdout = worker.process.stdout  # type: ignore[attr-defined]
    # The reader reads the very first frame only; heartbeat thread starts
    # AFTER the worker emits register, so there is no race here.
    line = await asyncio.wait_for(stdout.readline(), timeout=5.0)
    obj = json.loads(line.decode("utf-8").rstrip("\r\n"))
    assert obj.get("kind") == "register", (
        f"expected register frame, got kind={obj.get('kind')!r}"
    )
    return obj


@pytest.mark.integration
async def test_job_object_kills_unbounded_blast_worker(
    oom_blast_script: Path,
) -> None:
    """Bind 64 MiB Job Object; worker tries 512 MiB; kernel MUST kill it."""
    supervisor = WorkerSupervisor()
    limits = ResourceLimits(memory_mb=64, cpu_pct=50, wall_clock_ms=30_000)
    stderr_text = ""
    try:
        worker = await supervisor.spawn(oom_blast_script)

        register = await _read_register_frame(worker)
        declared_mb = register["registration"]["resourceLimits"]["memory_mb"]
        assert declared_mb == 64, (
            f"worker self-declared {declared_mb} MiB, expected 64"
        )

        # Late-bind the Job Object.
        bound = supervisor.bind_sandbox(worker.worker_id, limits)
        assert bound is not None, (
            "bind_sandbox returned None — Job Object was NOT attached; "
            "either _bind_job_object aborted (CreateJobObjectW failed) "
            "or the worker already exited."
        )
        assert "job_handle" in bound, (
            f"bind_sandbox returned {bound!r} without a job_handle key"
        )
        expected_cap_bytes = 64 * 1024 * 1024
        assert bound.get("memory_cap_bytes") == expected_cap_bytes, (
            f"bind_sandbox stored memory_cap_bytes="
            f"{bound.get('memory_cap_bytes')!r}, expected {expected_cap_bytes}"
        )

        # Fire the blast. The worker will allocate + touch 16 MiB chunks
        # without any self-limit until either the kernel kills it (good)
        # or it reaches its own 512 MiB safety ceiling (bad — failure).
        assert worker.process.stdin is not None
        await write_frame(
            worker.process.stdin,
            DispatchFrame(
                kind="dispatch",
                traceId="01KPQOOMTRACE000000000000001",
                taskId="01KPQOOMTEST000000000000001",
                capability="oom.blast",
                payload={"text": "blast"},
                budget=Budget(
                    wall_clock_ms=30_000, max_tool_calls=1, max_tokens=1_000
                ),
                deadline=datetime.now(tz=UTC) + timedelta(seconds=30),
            ),
        )

        # Wait for the subprocess to die. Job Object kill usually lands
        # in < 1 s on a modern machine; give generous headroom.
        returncode = await asyncio.wait_for(
            worker.process.wait(), timeout=30.0
        )

        # Drain stderr AFTER wait() — by now the pipe is closed and the
        # read returns immediately.
        assert worker.process.stderr is not None
        stderr_bytes = await worker.process.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        # Primary assertion: Job Object killed us before we reached the
        # cooperative ceiling. If this fires, Job Object silently did
        # nothing and the worker cruised through 512 MiB.
        assert "reached safety ceiling" not in stderr_text, (
            f"Job Object did NOT enforce memory_mb=64 — worker self-exited "
            f"after hitting the {512} MiB safety ceiling.\n"
            f"returncode={returncode}\n"
            f"--- stderr ---\n{stderr_text}"
        )

        # Secondary sanity: exit code is non-zero (abnormal). The exact
        # value varies: TerminateProcess from a Job Object memory cap
        # usually surfaces as a non-zero Windows exit code; we do not
        # pin a specific value because it depends on Windows version
        # and whether the allocation itself raised an exception first.
        assert returncode != 0, (
            f"expected non-zero exit after Job Object kill; got 0.\n"
            f"--- stderr ---\n{stderr_text}"
        )

        # Tertiary quality gate: the worker should have allocated only a
        # handful of chunks before dying. If we see > 8 chunks (i.e.
        # > 128 MiB) the Job Object was loose or not attached.
        chunks_seen = [
            line
            for line in stderr_text.splitlines()
            if "chunk" in line and "allocated" in line
        ]
        assert len(chunks_seen) <= 8, (
            f"Job Object permitted {len(chunks_seen)} x 16 MiB chunks "
            f"(~{len(chunks_seen) * 16} MiB) before killing the worker; "
            f"expected <= 8 (128 MiB) with a 64 MiB cap.\n"
            f"--- stderr ---\n{stderr_text}"
        )
    finally:
        await supervisor.shutdown(grace_s=0.5)
