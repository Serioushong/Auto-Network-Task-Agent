"""T057 — HIGH_RISK approval gate (INV-3 / FR-010~FR-012).

Purpose
-------
Enforce that every HIGH_RISK leaf_action Task is parked in
``pending_approval`` and released to ``dispatched`` only after a valid
``ApprovalResponse`` from the original user arrives (FR-012). Default
window 10 minutes (FR-011); any entry past its ``expiresAt`` gets
reported by ``sweep_expired`` so callers can walk Tasks to
``denied_by_timeout``.

Scope
-----
This module owns **state + decision logic**. Actual Task state
transitions, audit writes, and async wiring (timer, notifier) live in
``cli_main.KernelHarness`` — keeping the gate pure makes T056 unit
tests deterministic (no sleep, clock is injected).

Public surface
--------------
* ``ApprovalGate.register(task, user_id, *, window_ms=None)`` — records
  a new entry, returns ``PendingApprovalEntry`` containing the
  ``ApprovalRequest`` to be emitted.
* ``ApprovalGate.handle_response(response)`` — validates the response
  (identity, freshness), removes the entry, returns an ``ApprovalOutcome``
  describing whether to approve / deny.
* ``ApprovalGate.sweep_expired(now=None)`` — returns+removes every
  entry whose ``expiresAt <= now``; caller drives the
  ``denied_by_timeout`` transition.
* ``ApprovalGate.is_pending(task_id)`` — introspection for tests.

Invariants
----------
* An ``ApprovalResponse`` from a non-registering user never consumes a
  pending slot (``impersonation_rejected`` keeps the entry alive) —
  protects against "approval 洪水 by attacker" (spec.md edge case).
* Every successful consumption (``approved`` / ``denied`` /
  ``denied_by_timeout``) MUST delete the entry so a stale duplicate
  raises ``ApprovalStaleError``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from ..contracts.approval import ApprovalRequest, ApprovalResponse
from ..contracts.task import Task

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalOutcome",
    "ApprovalStaleError",
    "PendingApprovalEntry",
]

DEFAULT_APPROVAL_WINDOW_MS = 600_000  # FR-011


class ApprovalDecision(Enum):
    """Terminal verdict for one approval entry."""

    approved = "approved"
    denied = "denied"
    denied_by_timeout = "denied_by_timeout"
    impersonation_rejected = "impersonation_rejected"


class ApprovalStaleError(LookupError):
    """Raised when a response addresses a trace with no pending entry."""


@dataclass(frozen=True)
class PendingApprovalEntry:
    """Internal bookkeeping element + the ApprovalRequest frame."""

    task_id: str
    trace_id: str
    user_id: str
    request: ApprovalRequest
    expires_at: datetime


@dataclass(frozen=True)
class ApprovalOutcome:
    """Pure-data decision handed back to the kernel.

    ``user_id`` is the user who *caused* the decision: the original
    registrant on timeout, the responder otherwise.
    """

    decision: ApprovalDecision
    task_id: str
    trace_id: str
    user_id: str


def _default_clock() -> datetime:
    return datetime.now(tz=UTC)


class ApprovalGate:
    """In-memory HIGH_RISK approval registry.

    Not thread-safe on its own; callers run on a single asyncio loop so
    state access is naturally serialised. If a future phase spawns
    multiple loops, wrap mutators in a ``threading.Lock`` the same way
    the idempotency cache does.
    """

    def __init__(
        self,
        *,
        default_window_ms: int = DEFAULT_APPROVAL_WINDOW_MS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._default_window_ms = default_window_ms
        self._clock = clock or _default_clock
        self._by_task: dict[str, PendingApprovalEntry] = {}
        self._by_trace: dict[str, str] = {}  # traceId -> taskId

    # --- lifecycle --------------------------------------------------------

    def register(
        self,
        *,
        task: Task,
        user_id: str,
        window_ms: int | None = None,
    ) -> PendingApprovalEntry:
        """Open an approval window for ``task``.

        Raises:
            ValueError: task is not HIGH_RISK leaf_action in ``pending``.
        """
        if task.riskLevel != "HIGH_RISK":
            raise ValueError(
                f"approval_gate only accepts HIGH_RISK tasks; got {task.riskLevel!r}"
            )
        if task.kind != "leaf_action":
            raise ValueError(
                f"approval_gate only accepts leaf_action tasks; got {task.kind!r}"
            )
        if task.state != "pending":
            raise ValueError(
                f"approval_gate only accepts tasks in state 'pending'; got {task.state!r}"
            )

        window = window_ms if window_ms is not None else self._default_window_ms
        expires_at = self._clock() + timedelta(milliseconds=window)
        request = ApprovalRequest(
            kind="approval_request",
            traceId=task.traceId,
            taskId=task.taskId,
            capability=task.capability or "",
            riskLevel="HIGH_RISK",
            summary=_summarise_payload(task),
            expiresAt=expires_at,
        )
        entry = PendingApprovalEntry(
            task_id=task.taskId,
            trace_id=task.traceId,
            user_id=user_id,
            request=request,
            expires_at=expires_at,
        )
        self._by_task[task.taskId] = entry
        self._by_trace[task.traceId] = task.taskId
        return entry

    def handle_response(self, response: ApprovalResponse) -> ApprovalOutcome:
        """Resolve ``response`` against the current pending entry.

        Raises:
            ApprovalStaleError: the trace has no pending entry (already
                resolved, or never registered).
        """
        task_id = self._by_trace.get(response.traceId)
        if task_id is None:
            raise ApprovalStaleError(
                f"no pending approval for traceId={response.traceId!r}"
            )
        entry = self._by_task[task_id]

        if response.userId != entry.user_id:
            return ApprovalOutcome(
                decision=ApprovalDecision.impersonation_rejected,
                task_id=entry.task_id,
                trace_id=entry.trace_id,
                user_id=response.userId,
            )

        decision = (
            ApprovalDecision.approved
            if response.decision == "approve"
            else ApprovalDecision.denied
        )
        self._remove(entry)
        return ApprovalOutcome(
            decision=decision,
            task_id=entry.task_id,
            trace_id=entry.trace_id,
            user_id=entry.user_id,
        )

    def sweep_expired(
        self, now: datetime | None = None
    ) -> list[ApprovalOutcome]:
        """Return + remove every entry whose deadline has passed."""
        current = now or self._clock()
        out: list[ApprovalOutcome] = []
        for entry in list(self._by_task.values()):
            if entry.expires_at <= current:
                self._remove(entry)
                out.append(
                    ApprovalOutcome(
                        decision=ApprovalDecision.denied_by_timeout,
                        task_id=entry.task_id,
                        trace_id=entry.trace_id,
                        user_id=entry.user_id,
                    )
                )
        return out

    # --- introspection ----------------------------------------------------

    def is_pending(self, task_id: str) -> bool:
        return task_id in self._by_task

    def get_entry(self, task_id: str) -> PendingApprovalEntry | None:
        return self._by_task.get(task_id)

    def next_deadline(self) -> datetime | None:
        """Earliest pending deadline, or ``None`` when empty."""
        if not self._by_task:
            return None
        return min(e.expires_at for e in self._by_task.values())

    # --- internals --------------------------------------------------------

    def _remove(self, entry: PendingApprovalEntry) -> None:
        self._by_task.pop(entry.task_id, None)
        if self._by_trace.get(entry.trace_id) == entry.task_id:
            self._by_trace.pop(entry.trace_id, None)


def _summarise_payload(task: Task) -> str:
    """Build a short human-readable blurb for the ApprovalRequest summary.

    Keeps the raw payload string bounded at 128 chars so the request
    never explodes past the schema's 512-char cap even under adversarial
    inputs. Sensitive fields are NOT redacted here — audit/redact.py is
    the chokepoint for that; the summary is intentionally a short echo
    for the operator to recognise the command.
    """
    payload = task.payload or {}
    blurb = (
        payload.get("path")
        or payload.get("summary")
        or payload.get("text")
        or ""
    )
    if isinstance(blurb, str) and len(blurb) > 128:
        blurb = blurb[:125] + "..."
    cap = task.capability or "unknown"
    if blurb:
        return f"{cap}: {blurb}"
    return cap
