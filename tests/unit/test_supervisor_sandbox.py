"""Phase N.2 RED — Supervisor sandbox binding (FR-025 / T074).

Pins the OS-level resource-cap contract for worker spawn:

* On Windows, ``WorkerSupervisor.spawn(..., resource_limits=...)`` MUST
  call ``_bind_job_object(pid, limits)`` which wraps the live child PID
  in a Windows Job Object with ``JOB_OBJECT_LIMIT_PROCESS_MEMORY``
  set to ``limits.memory_mb * 1024 * 1024`` and the
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` flag so the worker dies when
  the kernel does. The handle MUST be stashed on
  ``SupervisedWorker.metadata["job_handle"]`` so later teardown can
  close it.
* On POSIX, ``_build_preexec_fn(limits)`` returns a callable that, when
  run in the forked child, sets ``RLIMIT_AS`` to
  ``limits.memory_mb * 1024 * 1024`` before exec. The callable MUST be
  passed to ``asyncio.create_subprocess_exec(preexec_fn=...)`` — this
  file only asserts the callable behaviour because ``preexec_fn``
  isn't meaningful on Windows where the tests actually run.

Tests are platform-gated with ``sys.platform`` so the bytes on disk
remain green on both Windows CI and POSIX CI. The implementation lives
in ``src/orchestrator_kernel/worker_supervisor/supervisor.py`` and
fails RED because ``_bind_job_object`` is currently a no-op placeholder
and ``_build_preexec_fn`` does not exist yet.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from orchestrator_kernel.contracts.worker import ResourceLimits


@pytest.fixture
def limits_64mb() -> ResourceLimits:
    return ResourceLimits(memory_mb=64, cpu_pct=25, wall_clock_ms=5_000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only Job Object path")
def test_bind_job_object_creates_assigns_and_stashes_handle(
    limits_64mb: ResourceLimits,
) -> None:
    """`_bind_job_object` must drive the full ctypes create/assign sequence."""
    import ctypes

    from orchestrator_kernel.worker_supervisor import supervisor as sup_mod

    fake_kernel32 = MagicMock(name="kernel32")
    fake_kernel32.CreateJobObjectW.return_value = 0xDEAD_BEEF
    fake_kernel32.OpenProcess.return_value = 0xFEED_FACE
    fake_kernel32.SetInformationJobObject.return_value = 1
    fake_kernel32.AssignProcessToJobObject.return_value = 1
    fake_kernel32.CloseHandle.return_value = 1

    with patch.object(ctypes, "windll", create=True) as fake_windll:
        fake_windll.kernel32 = fake_kernel32
        metadata = sup_mod._bind_job_object(pid=12345, limits=limits_64mb)

    assert fake_kernel32.CreateJobObjectW.called, (
        "CreateJobObjectW must be invoked to create the Job Object handle"
    )
    assert fake_kernel32.SetInformationJobObject.called, (
        "SetInformationJobObject must apply the extended-limit struct"
    )
    args, _ = fake_kernel32.SetInformationJobObject.call_args
    # Arg layout per MSDN:
    #   SetInformationJobObject(hJob, JobObjectInformationClass=9,
    #                           lpJobObjectInfo, cbJobObjectInfoLength)
    assert args[0] == 0xDEAD_BEEF
    assert args[1] == 9, (  # JobObjectExtendedLimitInformation
        f"expected JobObjectExtendedLimitInformation class=9, got {args[1]!r}"
    )
    assert fake_kernel32.AssignProcessToJobObject.call_args.args[0] == 0xDEAD_BEEF
    assert metadata is not None
    assert metadata.get("job_handle") == 0xDEAD_BEEF
    assert metadata.get("memory_cap_bytes") == 64 * 1024 * 1024


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only rlimit path")
def test_build_preexec_fn_sets_rlimit_as(limits_64mb: ResourceLimits) -> None:
    """The returned callable must call `resource.setrlimit(RLIMIT_AS, ...)`."""
    from orchestrator_kernel.worker_supervisor import supervisor as sup_mod

    captured: list[tuple[int, tuple[int, int]]] = []

    def _fake_setrlimit(resource_id: int, bounds: tuple[int, int]) -> None:
        captured.append((resource_id, bounds))

    import resource as resource_mod

    with patch.object(resource_mod, "setrlimit", side_effect=_fake_setrlimit):
        preexec = sup_mod._build_preexec_fn(limits_64mb)
        assert preexec is not None
        preexec()

    assert captured, "preexec_fn must call resource.setrlimit at least once"
    resource_ids = {rid for rid, _ in captured}
    assert resource_mod.RLIMIT_AS in resource_ids, (
        f"expected RLIMIT_AS set; got {resource_ids!r}"
    )
    as_bounds = next(b for rid, b in captured if rid == resource_mod.RLIMIT_AS)
    expected_bytes = 64 * 1024 * 1024
    assert as_bounds[0] == expected_bytes, (
        f"RLIMIT_AS soft cap mismatch: want {expected_bytes}, got {as_bounds[0]}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only spawn wiring")
async def test_spawn_passes_resource_limits_into_job_object(
    tmp_path, limits_64mb: ResourceLimits
) -> None:
    """`WorkerSupervisor.spawn(..., resource_limits=...)` must call the binder."""
    from orchestrator_kernel.worker_supervisor import supervisor as sup_mod

    script = tmp_path / "worker.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdout.write('hi\\n'); sys.stdout.flush()\n"
        "time.sleep(0.5)\n",
        encoding="utf-8",
    )

    calls: list[tuple[int, ResourceLimits]] = []

    def _fake_bind(*, pid: int, limits: ResourceLimits) -> dict[str, object]:
        calls.append((pid, limits))
        return {"job_handle": 0xABCD, "memory_cap_bytes": limits.memory_mb * 1024 * 1024}

    with patch.object(sup_mod, "_bind_job_object", side_effect=_fake_bind):
        s = sup_mod.WorkerSupervisor()
        worker = await s.spawn(script, resource_limits=limits_64mb)
        assert calls, "spawn must call _bind_job_object when resource_limits is provided"
        assert calls[0][1] is limits_64mb
        assert worker.metadata.get("job_handle") == 0xABCD
        await worker.kill()
        await worker.wait()
