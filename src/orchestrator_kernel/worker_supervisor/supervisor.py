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
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ulid


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


def _bind_job_object(pid: int) -> None:
    """Placeholder for Windows Job Object binding (T074).

    MVP is a no-op. Once T074 lands, this function will:

    1. `CreateJobObjectW(None, None)` via `ctypes.windll.kernel32`;
    2. fill `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` with the
       per-capability `ResourceLimits` (mem cap, job-level CPU, kill-on-close);
    3. `AssignProcessToJobObject(hJob, OpenProcess(pid))`;
    4. stash `hJob` on `SupervisedWorker.metadata['job_handle']` so the
       handle is released on reaper / crash paths.

    Until then the kernel relies on psutil soft-monitoring (T073) for
    ResourceLimits enforcement and on process-group signalling for cancel.
    """
    # Intentional no-op; see T074.
    _ = pid


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
    ) -> SupervisedWorker:
        """Launch `python <script_path>` with stdio pipes and OS isolation.

        Args:
            script_path: Absolute path to the worker module. MUST exist.
            worker_id: Optional external id; auto-generated (ULID) otherwise.
            extra_args: CLI args appended after the script path.
            env: Optional env override. `None` inherits the kernel's env.

        Returns:
            A `SupervisedWorker` wrapping the live subprocess.

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

        try:
            if sys.platform == "win32":
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
                )
        except OSError as exc:
            raise WorkerSpawnError(
                f"failed to spawn worker {script_path}: {exc}"
            ) from exc

        _bind_job_object(process.pid)

        worker = SupervisedWorker(
            worker_id=wid,
            script_path=script_path,
            process=process,
        )
        self._workers[wid] = worker
        return worker

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
