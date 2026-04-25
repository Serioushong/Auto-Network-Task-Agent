"""T056 — ApprovalGate unit tests (RED first, GREEN after T057).

Pins the pure kernel-side approval behaviour without spinning up the full
harness:

* ``register(task, user_id)`` — must only accept HIGH_RISK leaf_action
  tasks in the ``pending`` state; records ``expiresAt = clock() +
  window_ms`` and returns the synthesised ``ApprovalRequest`` the
  notifier later hands to the source channel.
* ``handle_response(response, now=...)`` — returns one of
  ``approved | denied | impersonation_rejected | stale``; mutates
  in-memory bookkeeping so a second response on the same trace is
  ``stale`` (FR-012 + spec.md edge case "approval 与 cancel 竞态" — the
  stale branch protects us from double-fire).
* ``sweep_expired(now)`` — returns the list of pending entries that
  already crossed ``expiresAt``; caller transitions the Task state.

Controllable clock = a plain callable so tests never touch wall-clock
time. FR-011 default window = 10 minutes (600_000 ms).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orchestrator_kernel.contracts.approval import ApprovalResponse
from orchestrator_kernel.contracts.task import Task
from orchestrator_kernel.kernel.approval_gate import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalOutcome,
    ApprovalStaleError,
)

# --- fixtures ---------------------------------------------------------------


def _high_risk_task(
    *,
    trace_id: str = "01TRACE00000000000000000HR",
    task_id: str = "01TASK00000000000000000HR1",
    capability: str = "file.delete",
) -> Task:
    return Task.model_validate(
        {
            "taskId": task_id,
            "traceId": trace_id,
            "kind": "leaf_action",
            "parentTaskId": None,
            "capability": capability,
            "riskLevel": "HIGH_RISK",
            "budget": {
                "wall_clock_ms": 5_000,
                "max_tool_calls": 1,
                "max_tokens": 1_000,
            },
            "payload": {"path": "fake.txt"},
            "state": "pending",
            "createdAt": datetime(2026, 4, 21, tzinfo=UTC),
        }
    )


def _normal_task() -> Task:
    return Task.model_validate(
        {
            "taskId": "01TASK00000000000000000NORM",
            "traceId": "01TRACE00000000000000000N",
            "kind": "leaf_action",
            "parentTaskId": None,
            "capability": "echo.say",
            "riskLevel": "NORMAL",
            "budget": {
                "wall_clock_ms": 5_000,
                "max_tool_calls": 1,
                "max_tokens": 1_000,
            },
            "payload": {"text": "hi"},
            "state": "pending",
            "createdAt": datetime(2026, 4, 21, tzinfo=UTC),
        }
    )


class _Clock:
    """Monotonic-ish clock driver; tests bump it explicitly."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# --- register ---------------------------------------------------------------


def test_register_stamps_expires_at_at_now_plus_window() -> None:
    start = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    clock = _Clock(start)
    gate = ApprovalGate(default_window_ms=600_000, clock=clock)

    task = _high_risk_task()
    entry = gate.register(task=task, user_id="alice")

    assert entry.request.riskLevel == "HIGH_RISK"
    assert entry.request.traceId == task.traceId
    assert entry.request.taskId == task.taskId
    assert entry.request.capability == "file.delete"
    assert entry.request.expiresAt == start + timedelta(minutes=10)
    assert entry.user_id == "alice"


def test_register_rejects_non_high_risk_task() -> None:
    gate = ApprovalGate()
    with pytest.raises(ValueError, match="HIGH_RISK"):
        gate.register(task=_normal_task(), user_id="alice")


def test_register_rejects_non_pending_task() -> None:
    gate = ApprovalGate()
    task = _high_risk_task().model_copy(update={"state": "dispatched"})
    with pytest.raises(ValueError, match="pending"):
        gate.register(task=task, user_id="alice")


def test_register_custom_window_overrides_default() -> None:
    start = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    clock = _Clock(start)
    gate = ApprovalGate(default_window_ms=600_000, clock=clock)

    entry = gate.register(
        task=_high_risk_task(), user_id="alice", window_ms=60_000
    )
    assert entry.request.expiresAt == start + timedelta(seconds=60)


# --- handle_response --------------------------------------------------------


def test_handle_response_approve_returns_approved() -> None:
    start = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    clock = _Clock(start)
    gate = ApprovalGate(clock=clock)
    task = _high_risk_task()
    gate.register(task=task, user_id="alice")

    response = ApprovalResponse(
        kind="approval_response",
        traceId=task.traceId,
        decision="approve",
        userId="alice",
        receivedAt=start + timedelta(seconds=5),
    )
    outcome = gate.handle_response(response)

    assert outcome.decision is ApprovalDecision.approved
    assert outcome.task_id == task.taskId


def test_handle_response_deny_returns_denied() -> None:
    gate = ApprovalGate()
    task = _high_risk_task()
    gate.register(task=task, user_id="alice")

    response = ApprovalResponse(
        kind="approval_response",
        traceId=task.traceId,
        decision="deny",
        userId="alice",
        receivedAt=datetime.now(tz=UTC),
    )
    outcome = gate.handle_response(response)

    assert outcome.decision is ApprovalDecision.denied


def test_handle_response_foreign_user_is_impersonation_rejected() -> None:
    gate = ApprovalGate()
    task = _high_risk_task()
    gate.register(task=task, user_id="alice")

    response = ApprovalResponse(
        kind="approval_response",
        traceId=task.traceId,
        decision="approve",
        userId="mallory",
        receivedAt=datetime.now(tz=UTC),
    )
    outcome = gate.handle_response(response)

    assert outcome.decision is ApprovalDecision.impersonation_rejected
    assert gate.is_pending(task.taskId) is True, (
        "impersonation MUST NOT consume the pending slot; alice can still approve"
    )


def test_handle_response_twice_second_is_stale() -> None:
    gate = ApprovalGate()
    task = _high_risk_task()
    gate.register(task=task, user_id="alice")

    resp = ApprovalResponse(
        kind="approval_response",
        traceId=task.traceId,
        decision="approve",
        userId="alice",
        receivedAt=datetime.now(tz=UTC),
    )
    _ = gate.handle_response(resp)

    with pytest.raises(ApprovalStaleError):
        gate.handle_response(resp)


def test_handle_response_on_unknown_trace_is_stale() -> None:
    gate = ApprovalGate()
    response = ApprovalResponse(
        kind="approval_response",
        traceId="01TRACENONEXISTXXXXXXXXXXX",
        decision="approve",
        userId="alice",
        receivedAt=datetime.now(tz=UTC),
    )
    with pytest.raises(ApprovalStaleError):
        gate.handle_response(response)


# --- sweep_expired ----------------------------------------------------------


def test_sweep_expired_returns_entries_past_deadline() -> None:
    start = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    clock = _Clock(start)
    gate = ApprovalGate(default_window_ms=10_000, clock=clock)

    task_a = _high_risk_task(task_id="01TASKAAA00000000000000000")
    task_b = _high_risk_task(
        task_id="01TASKBBB00000000000000000",
        trace_id="01TRACE00000000000000000HB",
    )
    gate.register(task=task_a, user_id="alice")
    gate.register(task=task_b, user_id="bob")

    clock.advance(5)
    assert gate.sweep_expired() == []

    clock.advance(6)
    expired = gate.sweep_expired()

    task_ids = {e.task_id for e in expired}
    assert task_ids == {task_a.taskId, task_b.taskId}
    for e in expired:
        assert e.decision is ApprovalDecision.denied_by_timeout
    assert gate.is_pending(task_a.taskId) is False
    assert gate.is_pending(task_b.taskId) is False


def test_sweep_expired_no_effect_when_window_not_elapsed() -> None:
    start = datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)
    clock = _Clock(start)
    gate = ApprovalGate(default_window_ms=60_000, clock=clock)
    gate.register(task=_high_risk_task(), user_id="alice")

    clock.advance(30)
    assert gate.sweep_expired() == []


# --- ApprovalOutcome dataclass surface -------------------------------------


def test_outcome_dataclass_shape() -> None:
    outcome = ApprovalOutcome(
        decision=ApprovalDecision.approved,
        task_id="01TASK00000000000000000HR1",
        trace_id="01TRACE00000000000000000HR",
        user_id="alice",
    )
    assert outcome.decision.value == "approved"
    assert outcome.task_id == "01TASK00000000000000000HR1"
