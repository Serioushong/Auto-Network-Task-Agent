"""T041 — Subprocess worker supervisor (spawn + isolation placeholders).

Responsibilities owned here for US1 MVP:

- spawn a Worker script (`python <path>`) as a child process with pipes on
  stdin/stdout/stderr;
- give it its own process group on Windows
  (`subprocess.CREATE_NEW_PROCESS_GROUP`) so later `CTRL_BREAK_EVENT`
  propagation (T068) hits only the worker tree and not the kernel;
- on POSIX use `start_new_session=True` for the same isolation property;
- expose the resulting `SupervisedWorker` DTO so the dispatcher / protocol
  layers can correlate `worker_id` to a live `asyncio.subprocess.Process`.

Intentionally **out of scope** for this batch (placeholders only):

- Windows **Job Object** binding (`CreateJobObjectW` + `AssignProcessToJobObject`
  + `SetInformationJobObject(JobObjectExtendedLimitInformation)`). The
  `_bind_job_object()` hook lives below and is a no-op until T074.
- psutil-driven resource sampling / `sandbox_limit` escalation — T073.
- heartbeat timeout / unhealthy flipping — T076 (lifecycle.py).

Nothing in T039~T042 calls `WorkerSupervisor.spawn()` yet; the cli_main
pipeline (T045) will be the first site that does. Until then this module
is reachable only in unit tests, which is intentional — the integration
tests (test_p1_*) drive the full assembly and remain RED until T043/T045.
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ulid

from ..contracts.worker import ResourceLimits


class SupervisorError(Exception):
    """Base class for supervisor-raised errors."""


class WorkerSpawnError(SupervisorError):
    """Raised when the subprocess cannot be started (script missing, OS error)."""


@dataclass
class SupervisedWorker:
    """Live handle over one running worker subprocess.

    Not thread-safe: the kernel event loop is the sole owner. Callers use
    `process.stdin` / `process.stdout` directly with the helpers in
    `worker_supervisor.protocol`.
    """

    worker_id: str
    script_path: Path
    process: asyncio.subprocess.Process
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        """Block until the worker exits; returns its exit code."""
        return await self.process.wait()

    async def terminate(self) -> None:
        """Best-effort graceful terminate; swallows `ProcessLookupError`.

        Full signal-escalation (soft -> 3s -> terminate -> 1s -> kill) lands
        in `lifecycle.py` per T068. This hook exists so cleanup in tests
        / `shutdown()` paths has one place to call.
        """
        if self.process.returncode is not None:
            return
        try:
            self.process.terminate()
        except ProcessLookupError:
            pass

    async def kill(self) -> None:
        """Hard kill; swallows `ProcessLookupError` if the child already exited."""
        if self.process.returncode is not None:
            return
        try:
            self.process.kill()
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------------------
# T074 — OS-level sandbox binding (Job Object on Windows, rlimit on POSIX).
# ---------------------------------------------------------------------------


# Windows constants (from winnt.h / jobapi.h).
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_PROCESS_ALL_ACCESS = 0x1F0FFF
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
# Child-process creation flag that detaches it from the caller's Job
# Object so our own Job Object's ``ProcessMemoryLimit`` can actually
# enforce (see ``_bind_job_object`` and Evidence #14 for why nested-job
# memory limits are effectively a no-op under VSCode/WT parent jobs).
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Default per-process memory ceiling applied at spawn time when the
# caller has not yet declared per-worker limits. Prevents runaway
# workers even before their register frame arrives. Deliberately
# generous (1 GiB) — real workloads tighten this via ``bind_sandbox``
# once the register frame lands. Kept as a module-level constant so
# tests can monkeypatch if they need a very narrow ceiling.
_DEFAULT_SPAWN_MEMORY_CEILING_MB = 1024


class _IO_COUNTERS(ctypes.Structure):
    """ctypes mirror of IO_COUNTERS (used inside the extended-limit struct)."""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """ctypes mirror of JOBOBJECT_BASIC_LIMIT_INFORMATION."""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """ctypes mirror of JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _configure_kernel32_signatures(kernel32: Any) -> None:
    """Pin ``argtypes`` / ``restype`` on every kernel32 call we use.

    Critical bug guard (Evidence #14): ``ctypes`` defaults ``restype``
    to ``c_int`` (32-bit) for every foreign function. On 64-bit Windows
    ``HANDLE`` is pointer-sized (64-bit), so the HANDLE returned by
    ``CreateJobObjectW`` gets **silently truncated** to 32 bits. The
    follow-on ``SetInformationJobObject`` / ``AssignProcessToJobObject``
    calls then operate on a mangled handle value — neither fails with
    an obvious error (they may "succeed" on a random handle or on
    ``NULL``), but the Job Object never gets bound to the real process
    so memory limits silently don't apply.

    Setting these once at module-import time is idempotent and costs
    nothing at call time. ``c_void_p`` is the portable stand-in for
    ``HANDLE`` — pointer-sized on both 32- and 64-bit builds.
    """
    HANDLE = ctypes.c_void_p
    DWORD = ctypes.c_ulong
    BOOL = ctypes.c_int
    LPCWSTR = ctypes.c_wchar_p
    LPVOID = ctypes.c_void_p

    kernel32.CreateJobObjectW.argtypes = [LPVOID, LPCWSTR]
    kernel32.CreateJobObjectW.restype = HANDLE

    kernel32.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, LPVOID, DWORD]
    kernel32.SetInformationJobObject.restype = BOOL

    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE

    kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
    kernel32.AssignProcessToJobObject.restype = BOOL

    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL


def _bind_job_object(*, pid: int, limits: ResourceLimits) -> dict[str, Any] | None:
    """Wrap ``pid`` in a Windows Job Object with per-process memory cap.

    Returns the new metadata dict (``{"job_handle": <HANDLE>,
    "memory_cap_bytes": <int>}``) so ``WorkerSupervisor.spawn`` can stash
    it on ``SupervisedWorker.metadata`` for later teardown
    (``kernel32.CloseHandle``) and for the audit event.

    On non-Windows platforms the function is a no-op and returns
    ``None`` — POSIX uses ``_build_preexec_fn`` to set
    ``RLIMIT_AS`` in the forked child before exec instead.

    Failures log to stderr but never raise: the kernel treats sandbox
    binding as best-effort so a hardened host without sufficient
    privileges still gets the soft monitor path.
    """
    if sys.platform != "win32":
        return None

    kernel32 = ctypes.windll.kernel32
    _configure_kernel32_signatures(kernel32)

    h_job = kernel32.CreateJobObjectW(None, None)
    if not h_job:
        return None

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    mem_cap = int(limits.memory_mb) * 1024 * 1024
    info.ProcessMemoryLimit = mem_cap

    ok = kernel32.SetInformationJobObject(
        h_job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        try:
            kernel32.CloseHandle(h_job)
        except Exception:  # noqa: BLE001
            pass
        return None

    h_process = kernel32.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
    if not h_process:
        # Fall back to a lesser access mask; some sandboxes refuse ALL_ACCESS.
        h_process = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
        )

    if not h_process:
        try:
            kernel32.CloseHandle(h_job)
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        if not kernel32.AssignProcessToJobObject(h_job, h_process):
            try:
                kernel32.CloseHandle(h_job)
            except Exception:  # noqa: BLE001
                pass
            return None
    finally:
        try:
            kernel32.CloseHandle(h_process)
        except Exception:  # noqa: BLE001
            pass

    return {"job_handle": int(h_job), "memory_cap_bytes": mem_cap}


def _update_job_memory_cap(job_handle: int, limits: ResourceLimits) -> bool:
    """Re-configure an existing Job Object's ``ProcessMemoryLimit`` in place.

    Used by ``bind_sandbox`` to tighten the generous spawn-time default
    once the Worker's ``RegisterFrame`` has delivered its declared
    limits. MSDN guarantees that calling ``SetInformationJobObject``
    twice simply replaces the previously-established limit of the same
    type — no need to destroy / recreate the Job Object, which would
    otherwise require another ``AssignProcessToJobObject`` round-trip.

    Returns ``True`` on success, ``False`` on any ctypes-reported
    failure. Failures are intentionally silent: the conservative
    spawn-time ceiling remains in effect as a fallback.
    """
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    _configure_kernel32_signatures(kernel32)
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    info.ProcessMemoryLimit = int(limits.memory_mb) * 1024 * 1024
    ok = kernel32.SetInformationJobObject(
        ctypes.c_void_p(job_handle),
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    return bool(ok)


def _build_preexec_fn(limits: ResourceLimits) -> Callable[[], None] | None:
    """Return a child-side ``preexec_fn`` that pins ``RLIMIT_AS`` (POSIX only).

    On Windows (where preexec_fn is meaningless) returns ``None`` so
    ``asyncio.create_subprocess_exec`` sees no extra argument. On POSIX,
    the returned callable is invoked in the forked child right before
    exec, giving the kernel a hard address-space cap. CPU limits are
    out of scope here — POSIX ``RLIMIT_CPU`` is wall-clock-agnostic and
    would fight the existing wall-budget path.
    """
    if sys.platform == "win32":
        return None
    import resource

    mem_cap = int(limits.memory_mb) * 1024 * 1024

    def _apply() -> None:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_cap, mem_cap))
        except (OSError, ValueError):
            # A hardened host may deny rlimit tightening; keep going and
            # rely on the soft monitor (watch_worker) instead.
            pass

    return _apply


def _release_job_handle(metadata: Mapping[str, Any] | None) -> None:
    """Best-effort ``CloseHandle`` for the Job Object tracked in metadata."""
    if not metadata:
        return
    handle = metadata.get("job_handle")
    if handle is None:
        return
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        _configure_kernel32_signatures(kernel32)
        kernel32.CloseHandle(ctypes.c_void_p(int(handle)))
    except Exception:  # noqa: BLE001
        pass


class WorkerSupervisor:
    """Owns subprocess spawn + isolation primitives for the worker fleet.

    A kernel instance holds exactly one supervisor. Nothing in this class
    persists across kernel restarts — the audit scanner (T082) is what
    rebuilds state after crash.
    """

    def __init__(self, *, python_executable: str | None = None) -> None:
        self._python = python_executable or sys.executable
        self._workers: dict[str, SupervisedWorker] = {}

    @property
    def python_executable(self) -> str:
        return self._python

    def living(self) -> tuple[SupervisedWorker, ...]:
        """Snapshot of workers whose subprocess has not yet reaped."""
        return tuple(
            w for w in self._workers.values() if w.process.returncode is None
        )

    async def spawn(
        self,
        script_path: Path,
        *,
        worker_id: str | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> SupervisedWorker:
        """Launch `python <script_path>` with stdio pipes and OS isolation.

        Args:
            script_path: Absolute path to the worker module. MUST exist.
            worker_id: Optional external id; auto-generated (ULID) otherwise.
            extra_args: CLI args appended after the script path.
            env: Optional env override. `None` inherits the kernel's env.
            resource_limits: Optional OS-level sandbox bounds. When set,
                the child is wrapped in a Windows Job Object (T074) or,
                on POSIX, spawned with a ``preexec_fn`` that pins
                ``RLIMIT_AS`` before exec.

        Returns:
            A `SupervisedWorker` wrapping the live subprocess. If
            ``resource_limits`` was honoured, the returned worker's
            ``metadata`` dict carries the Job Object handle + memory cap.

        Raises:
            WorkerSpawnError: Script missing or `asyncio.create_subprocess_exec`
                raised an OSError.
        """
        if not script_path.exists():
            raise WorkerSpawnError(
                f"worker script not found: {script_path}"
            )

        wid = worker_id or f"worker-{str(ulid.new().str).lower()}"
        if wid in self._workers:
            raise WorkerSpawnError(
                f"supervisor already tracking worker_id={wid!r}"
            )

        argv: tuple[str, ...] = (self._python, str(script_path), *extra_args)
        env_arg: dict[str, str] | None = dict(env) if env is not None else None

        preexec = (
            _build_preexec_fn(resource_limits)
            if resource_limits is not None
            else None
        )

        try:
            if sys.platform == "win32":
                # ``CREATE_BREAKAWAY_FROM_JOB`` is critical (Evidence #14):
                # when the kernel itself is launched inside VSCode, Windows
                # Terminal, or any other Job Object–wrapping host, the child
                # inherits that job. Our subsequent ``AssignProcessToJobObject``
                # call will *succeed* (Win8+ nested jobs are legal) yet the
                # ``ProcessMemoryLimit`` we set silently fails to bite.
                # Breaking away first puts the child into a plain process
                # tree so our Job Object is authoritative for memory bounds.
                # Falls back to the same spawn without the flag if the
                # parent job forbids breakaway (``ERROR_ACCESS_DENIED``).
                creation_flags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | _CREATE_BREAKAWAY_FROM_JOB
                )
                try:
                    process = await asyncio.create_subprocess_exec(
                        *argv,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env_arg,
                        creationflags=creation_flags,
                    )
                except OSError:
                    process = await asyncio.create_subprocess_exec(
                        *argv,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env_arg,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
            else:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env_arg,
                    start_new_session=True,
                    preexec_fn=preexec,  # type: ignore[arg-type]
                )
        except OSError as exc:
            raise WorkerSpawnError(
                f"failed to spawn worker {script_path}: {exc}"
            ) from exc

        metadata: dict[str, Any] = {}
        if sys.platform == "win32":
            # Evidence #14 two-stage sandbox: bind the Job Object here,
            # BEFORE the asyncio reactor services the child's stdio
            # pipes, with a conservative default ceiling. Empirically
            # if we wait until after the first ``await stdout.readline()``
            # the kernel still reports ``IsProcessInJob=True`` but the
            # ``ProcessMemoryLimit`` silently stops biting on a nested
            # Job under VSCode/Windows Terminal hosts. Binding before
            # any I/O on the pipe avoids that window.
            # If the caller pre-declared limits use them; otherwise
            # drop in ``_DEFAULT_SPAWN_MEMORY_CEILING_MB`` so the
            # register-time ``bind_sandbox`` can tighten via
            # ``SetInformationJobObject``.
            initial_limits = resource_limits or ResourceLimits(
                memory_mb=_DEFAULT_SPAWN_MEMORY_CEILING_MB,
                cpu_pct=100,
                wall_clock_ms=600_000,
            )
            job_meta = _bind_job_object(
                pid=process.pid, limits=initial_limits
            )
            if job_meta is not None:
                metadata.update(job_meta)

        worker = SupervisedWorker(
            worker_id=wid,
            script_path=script_path,
            process=process,
            metadata=metadata,
        )
        self._workers[wid] = worker
        return worker

    def bind_sandbox(
        self, worker_id: str, limits: ResourceLimits
    ) -> dict[str, Any] | None:
        """Tighten (or create) the OS sandbox for an already-spawned worker.

        The Worker's self-declared ``ResourceLimits`` arrive in its
        ``RegisterFrame`` *after* spawn, so the harness cannot pass
        them to ``spawn(resource_limits=...)`` up front.

          * **Windows** — ``spawn`` already bound a Job Object with a
            conservative default cap (see
            ``_DEFAULT_SPAWN_MEMORY_CEILING_MB``) before any pipe I/O,
            so the kernel's ``ProcessMemoryLimit`` enforcement is
            already active. This method merely re-runs
            ``SetInformationJobObject`` on the existing job handle to
            **tighten** the per-process cap to the worker's declared
            ``memory_mb``. If for some reason the job handle is
            missing (spawn fallback path, privilege error, etc.) this
            falls through to a full ``CreateJobObjectW`` +
            ``AssignProcessToJobObject`` pair as a best effort.
          * **POSIX** — no-op: ``RLIMIT_AS`` MUST be set at fork time
            via ``preexec_fn`` and cannot be retrofitted to a running
            process. Callers who need POSIX enforcement should pass
            ``resource_limits`` to ``spawn`` directly.

        Returns the updated metadata slice on success, ``None`` if the
        worker is gone / already terminated / late-bind skipped.
        """
        worker = self._workers.get(worker_id)
        if worker is None:
            return None
        if worker.process.returncode is not None:
            return None
        if sys.platform != "win32":
            return None

        existing_handle = worker.metadata.get("job_handle")
        if existing_handle is not None:
            if _update_job_memory_cap(int(existing_handle), limits):
                mem_cap = int(limits.memory_mb) * 1024 * 1024
                worker.metadata["memory_cap_bytes"] = mem_cap
                return {
                    "job_handle": int(existing_handle),
                    "memory_cap_bytes": mem_cap,
                }
            return None

        meta = _bind_job_object(pid=worker.process.pid, limits=limits)
        if meta is None:
            return None
        worker.metadata.update(meta)
        return meta

    async def shutdown(self, *, grace_s: float = 1.0) -> None:
        """Terminate every living worker. Best-effort; swallows per-worker errors.

        Order: send `terminate()`, wait up to `grace_s`, then `kill()` the
        remainder. Final signal-escalation policy is refined in T068; this
        exists so test teardown / ctrl-C paths have a single entry point.
        """
        living = self.living()
        for worker in living:
            await worker.terminate()

        if grace_s > 0 and living:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(w.wait() for w in living),
                        return_exceptions=True,
                    ),
                    timeout=grace_s,
                )
            except TimeoutError:
                pass

        for worker in self.living():
            await worker.kill()

        # Release any Job Object handles we allocated so the Windows
        # kernel can reclaim them even if the underlying process was
        # already reaped by the Job Object itself.
        for worker in self._workers.values():
            _release_job_handle(worker.metadata)
