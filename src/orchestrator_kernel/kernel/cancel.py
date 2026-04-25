"""T065 — Trace-level cancel manager (FR-013 / FR-014 / FR-015).

Responsibilities owned here:

1. Keep a per-trace ``_Session`` that tracks
   (a) the original ``userId`` so cancel impersonation can be rejected,
   (b) every leaf currently in a non-terminal state, annotated with
       the worker that is dispatching it (or ``None`` for a
       ``pending_approval`` slot),
   (c) a single ``asyncio.Event`` that leaf-handling coroutines block
       on so they all unblock the moment cancel is accepted.
2. Arbitrate cancel requests through ``request_cancel``, returning a
   strongly-typed ``CancelOutcome`` so callers don't duplicate string
   literals: ``accepted | not_found | already_cancelled |
   impersonation_rejected | already_terminal``.
3. Stay audit-free: this module never writes audit events; it just
   decides state and wakes up blocked coroutines. The harness
   (``cli_main.KernelHarness``) owns the audit writes so the single
   source of truth for the event chain stays there (FR-006).

Why in-memory bookkeeping is enough for MVP
-------------------------------------------
The kernel is a single process (Constitution Article VIII) with a
single event loop owning all mutation. Cross-process cancel (CLI
``cancel <traceId>`` in a fresh process) depends on Phase 7 daemon
mode; the harness exposes ``request_cancel`` directly and integration
tests drive it in-process. An honest-disclosure entry in
``validation.md`` records the gap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

__all__ = [
    "CancelManager",
    "CancelOutcome",
    "CancelStatus",
]


class CancelStatus(Enum):
    """Discriminator for ``CancelOutcome.status``."""

    accepted = "accepted"
    not_found = "not_found"
    already_cancelled = "already_cancelled"
    already_terminal = "already_terminal"
    impersonation_rejected = "impersonation_rejected"


@dataclass(frozen=True)
class CancelOutcome:
    """Pure-data response handed back from ``CancelManager.request_cancel``."""

    status: CancelStatus
    trace_id: str
    user_id: str | None = None
    cancelled_task_ids: tuple[str, ...] = ()
    cancelled_worker_ids: tuple[str, ...] = ()


@dataclass
class _Session:
    """One live trace's cancel bookkeeping."""

    trace_id: str
    user_id: str
    event: asyncio.Event
    tasks: dict[str, str | None] = field(default_factory=dict)
    """``taskId -> workerId`` (``None`` for pending_approval slots)."""
    cancelled: bool = False
    terminal: bool = False
    cancelled_at: datetime | None = None


class CancelManager:
    """Per-harness cancel registry. Not thread-safe; single-loop only."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    # --- lifecycle -------------------------------------------------------

    def register_trace(self, *, trace_id: str, user_id: str) -> None:
        """Open a new cancel session when a submit starts a fresh trace.

        Idempotent: re-registering the same trace (e.g. after an
        idempotent replay is rejected and the caller decides to reopen)
        leaves existing state intact.
        """
        if trace_id in self._sessions:
            return
        self._sessions[trace_id] = _Session(
            trace_id=trace_id,
            user_id=user_id,
            event=asyncio.Event(),
        )

    def register_dispatch(
        self, *, trace_id: str, task_id: str, worker_id: str | None
    ) -> None:
        """Record that ``task_id`` is live under ``worker_id`` (or pending)."""
        session = self._sessions.get(trace_id)
        if session is None:
            return
        session.tasks[task_id] = worker_id

    def unregister_task(self, *, trace_id: str, task_id: str) -> None:
        """Drop a task from the session once it's reached a terminal state."""
        session = self._sessions.get(trace_id)
        if session is None:
            return
        session.tasks.pop(task_id, None)

    def mark_terminal(self, *, trace_id: str) -> None:
        """Mark the trace as completed; future cancels return ``already_terminal``."""
        session = self._sessions.get(trace_id)
        if session is None:
            return
        session.terminal = True

    def release(self, *, trace_id: str) -> None:
        """Drop the session entirely (call after submit returns)."""
        self._sessions.pop(trace_id, None)

    # --- query -----------------------------------------------------------

    def cancel_event(self, trace_id: str) -> asyncio.Event | None:
        """Return the asyncio Event callers should race against ``wait``."""
        session = self._sessions.get(trace_id)
        return session.event if session else None

    def is_cancelled(self, trace_id: str) -> bool:
        session = self._sessions.get(trace_id)
        return bool(session and session.cancelled)

    def user_for_trace(self, trace_id: str) -> str | None:
        session = self._sessions.get(trace_id)
        return session.user_id if session else None

    def snapshot(
        self, trace_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """Return ``(task_ids, worker_ids)`` currently tracked under ``trace_id``."""
        session = self._sessions.get(trace_id)
        if session is None:
            return None
        task_ids = tuple(session.tasks.keys())
        worker_ids = tuple(
            {wid for wid in session.tasks.values() if wid is not None}
        )
        return task_ids, worker_ids

    # --- cancel API ------------------------------------------------------

    async def request_cancel(
        self, *, trace_id: str, user_id: str
    ) -> CancelOutcome:
        """Accept / reject a cancel request for ``trace_id``.

        Impersonation check matches FR-012's approval semantics: only
        the original submitter may cancel. Cross-user cancel attempts
        are rejected without consuming the cancel slot.
        """
        session = self._sessions.get(trace_id)
        if session is None:
            return CancelOutcome(
                status=CancelStatus.not_found, trace_id=trace_id
            )
        if session.user_id != user_id:
            return CancelOutcome(
                status=CancelStatus.impersonation_rejected,
                trace_id=trace_id,
                user_id=user_id,
            )
        if session.terminal:
            return CancelOutcome(
                status=CancelStatus.already_terminal,
                trace_id=trace_id,
                user_id=user_id,
            )
        if session.cancelled:
            return CancelOutcome(
                status=CancelStatus.already_cancelled,
                trace_id=trace_id,
                user_id=user_id,
            )

        session.cancelled = True
        session.cancelled_at = datetime.now(tz=UTC)
        task_ids = tuple(session.tasks.keys())
        worker_ids = tuple(
            {wid for wid in session.tasks.values() if wid is not None}
        )
        session.event.set()
        return CancelOutcome(
            status=CancelStatus.accepted,
            trace_id=trace_id,
            user_id=user_id,
            cancelled_task_ids=task_ids,
            cancelled_worker_ids=worker_ids,
        )
