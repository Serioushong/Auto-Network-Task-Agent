"""T040 — Capability dispatcher (FR-008 / FR-009).

Matches a `Task.capability` against the set of currently registered, healthy
Worker handles. When no Worker declares the requested capability OR all
declaring Workers are `healthy=False`, the kernel MUST fail the task with
`failureReason="no_capable_worker"` (see tasks.md T037 and spec.md FR-008).

This module owns:

- an **in-memory registry** (`dict[workerId, WorkerHandle]`) — MVP single
  process, no external service discovery;
- a **capability index** kept in sync with the registry for O(1) lookup by
  capability name;
- the **assignment policy**: first healthy worker that declares the
  capability; ties are broken by ULID-sortable worker_id to stay
  deterministic for audit replay.

The dispatcher is **pure bookkeeping**: it does not own subprocess lifetime
(see `worker_supervisor.supervisor`) and does not perform I/O. The caller
(kernel pipeline, T045) is responsible for taking the chosen `WorkerHandle`
and writing a `DispatchFrame` via `worker_supervisor.protocol`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..contracts.task import Task
from ..contracts.worker import Capability, WorkerRegistration


class DispatcherError(Exception):
    """Base class for dispatcher-raised errors."""


class DuplicateWorkerError(DispatcherError):
    """A worker_id was registered twice without an intervening unregister()."""


class NoCapableWorkerError(DispatcherError):
    """No healthy worker declares the requested capability.

    Carries both the offending `capability` and a snapshot of why so the
    audit / ResultSummary path can explain "planner asked for X; registered
    workers provide Y".
    """

    def __init__(
        self,
        capability: str,
        *,
        known_capabilities: tuple[str, ...] = (),
        unhealthy_candidates: tuple[str, ...] = (),
    ) -> None:
        msg = f"no capable worker for capability={capability!r}"
        if unhealthy_candidates:
            msg += f"; unhealthy workers providing it: {list(unhealthy_candidates)}"
        elif known_capabilities:
            msg += f"; registered capabilities: {list(known_capabilities)}"
        super().__init__(msg)
        self.capability = capability
        self.known_capabilities = known_capabilities
        self.unhealthy_candidates = unhealthy_candidates


@dataclass
class WorkerHandle:
    """Dispatcher-visible facet of a spawned Worker subprocess.

    The dispatcher intentionally does not hold a reference to the subprocess
    itself — that lives in `worker_supervisor.supervisor.SupervisedWorker`,
    and the kernel pipeline correlates the two via `worker_id`. This keeps
    the dispatcher a pure data structure and makes it trivially unit-testable.
    """

    worker_id: str
    capabilities: tuple[Capability, ...]
    healthy: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_registration(
        cls, registration: WorkerRegistration, *, healthy: bool = True
    ) -> WorkerHandle:
        return cls(
            worker_id=registration.workerId,
            capabilities=tuple(registration.capabilities),
            healthy=healthy,
        )

    @property
    def capability_names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.capabilities)


class Dispatcher:
    """In-memory registry + capability matcher.

    Thread-safety: the kernel runs a single-writer event loop (anyio/asyncio),
    so this class does NOT take locks. If that assumption ever changes,
    wrap mutating methods in an `anyio.Lock` — never add per-method locks
    piecemeal.
    """

    def __init__(self) -> None:
        self._workers: dict[str, WorkerHandle] = {}
        self._by_capability: dict[str, list[str]] = {}

    def register(self, handle: WorkerHandle) -> None:
        """Add a new worker. Raises if `worker_id` already present."""
        if handle.worker_id in self._workers:
            raise DuplicateWorkerError(
                f"worker {handle.worker_id!r} already registered"
            )
        self._workers[handle.worker_id] = handle
        for cap_name in handle.capability_names:
            self._by_capability.setdefault(cap_name, []).append(handle.worker_id)
            self._by_capability[cap_name].sort()

    def unregister(self, worker_id: str) -> WorkerHandle | None:
        """Remove a worker; returns the removed handle or None if unknown."""
        handle = self._workers.pop(worker_id, None)
        if handle is None:
            return None
        for cap_name in handle.capability_names:
            bucket = self._by_capability.get(cap_name)
            if not bucket:
                continue
            if worker_id in bucket:
                bucket.remove(worker_id)
            if not bucket:
                self._by_capability.pop(cap_name, None)
        return handle

    def set_health(self, worker_id: str, *, healthy: bool) -> None:
        """Flip an existing worker's health flag (called by lifecycle heartbeat)."""
        handle = self._workers.get(worker_id)
        if handle is None:
            raise DispatcherError(f"unknown worker {worker_id!r}")
        handle.healthy = healthy

    def workers(self) -> Iterable[WorkerHandle]:
        """Read-only snapshot of registered handles."""
        return tuple(self._workers.values())

    def known_capabilities(self) -> tuple[str, ...]:
        """Sorted snapshot of capability names currently offered by any worker."""
        return tuple(sorted(self._by_capability.keys()))

    def find_for(self, capability: str) -> WorkerHandle | None:
        """Return the first healthy worker providing `capability`, else None."""
        candidate_ids = self._by_capability.get(capability, ())
        for wid in candidate_ids:
            handle = self._workers.get(wid)
            if handle is not None and handle.healthy:
                return handle
        return None

    def assign(self, task: Task) -> WorkerHandle:
        """Pick a worker for `task`; raise NoCapableWorkerError on miss.

        Preconditions (validated here so callers fail loud):
        - `task.kind == "leaf_action"` (root_intent is never dispatched)
        - `task.capability` is non-null
        """
        if task.kind != "leaf_action":
            raise DispatcherError(
                f"only leaf_action tasks can be dispatched; got kind={task.kind!r}"
            )
        if task.capability is None:
            raise DispatcherError("leaf_action.capability required for dispatch")

        handle = self.find_for(task.capability)
        if handle is not None:
            return handle

        unhealthy = tuple(
            wid
            for wid in self._by_capability.get(task.capability, ())
            if not self._workers[wid].healthy
        )
        raise NoCapableWorkerError(
            task.capability,
            known_capabilities=self.known_capabilities(),
            unhealthy_candidates=unhealthy,
        )
