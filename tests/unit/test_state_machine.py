"""T028 — failing unit test for Task state machine.

RED until T029 ships `orchestrator_kernel.kernel.state_machine.transition`.

Required invariants (INV-2 / INV-3 / FR-017):
- One-way transition table; any edge not in the table MUST raise.
- Terminal states (succeeded / failed / cancelled / denied / denied_by_timeout)
  have zero outgoing edges — any attempt to transition from them MUST raise.
- A leaf_action Task with riskLevel=HIGH_RISK MUST traverse `pending_approval`
  before it can be marked `dispatched` — a direct `pending -> dispatched` for
  HIGH_RISK is rejected even though the shape is legal for NORMAL tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orchestrator_kernel.contracts.budget import DEFAULT_BUDGET
from orchestrator_kernel.contracts.task import TERMINAL_STATES, Task, TaskState
from orchestrator_kernel.kernel.state_machine import ALLOWED_TRANSITIONS, transition

NOW = datetime(2026, 4, 21, tzinfo=UTC)


def _leaf(state: TaskState = "pending", *, risk: str = "NORMAL", **overrides: object) -> Task:
    base: dict[str, object] = {
        "taskId": "01J9LEAF0000000000",
        "parentTaskId": "01J9ROOT0000000000",
        "traceId": "01J9TRACE000000000",
        "kind": "leaf_action",
        "capability": "echo.say",
        "riskLevel": risk,
        "budget": DEFAULT_BUDGET.model_dump(),
        "state": state,
        "payload": {"message": "hi"},
        "createdAt": NOW,
    }
    base.update(overrides)
    return Task.model_validate(base)


class TestLegalTransitionsNormal:
    @pytest.mark.parametrize(
        "frm,to",
        [
            ("pending", "dispatched"),
            ("pending", "failed"),
            ("pending", "cancelled"),
            ("dispatched", "running"),
            ("dispatched", "failed"),
            ("dispatched", "cancelled"),
            ("running", "succeeded"),
            ("running", "failed"),
            ("running", "cancelled"),
        ],
    )
    def test_happy_edge(self, frm: TaskState, to: TaskState) -> None:
        t = _leaf(frm)
        out = transition(t, to)
        assert out.state == to
        if to in TERMINAL_STATES:
            assert out.outcome == to


class TestLegalTransitionsHighRisk:
    def test_high_risk_goes_via_pending_approval(self) -> None:
        t = _leaf("pending", risk="HIGH_RISK")
        t2 = transition(t, "pending_approval")
        assert t2.state == "pending_approval"

    def test_pending_approval_to_dispatched_after_approval(self) -> None:
        t = _leaf("pending_approval", risk="HIGH_RISK")
        t2 = transition(t, "dispatched")
        assert t2.state == "dispatched"

    def test_pending_approval_denied(self) -> None:
        t = _leaf("pending_approval", risk="HIGH_RISK")
        t2 = transition(t, "denied")
        assert t2.outcome == "denied"

    def test_pending_approval_denied_by_timeout(self) -> None:
        t = _leaf("pending_approval", risk="HIGH_RISK")
        t2 = transition(t, "denied_by_timeout")
        assert t2.outcome == "denied_by_timeout"


class TestHighRiskInvariantINV3:
    """HIGH_RISK MUST NOT bypass pending_approval."""

    def test_direct_pending_to_dispatched_rejected(self) -> None:
        t = _leaf("pending", risk="HIGH_RISK")
        with pytest.raises(ValueError, match="HIGH_RISK|INV-3"):
            transition(t, "dispatched")


class TestTerminalStatesINV2:
    @pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
    def test_no_outgoing_edges(self, terminal: TaskState) -> None:
        # Build a leaf Task in the given terminal state. Need outcome+state parity.
        t = _leaf(terminal, outcome=terminal)
        for to in ("pending", "dispatched", "running", "succeeded", "failed"):
            with pytest.raises(ValueError, match="terminal|INV-2"):
                transition(t, to)  # type: ignore[arg-type]


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        "frm,to",
        [
            ("pending", "succeeded"),  # must go through dispatched+running
            ("pending", "running"),
            ("pending", "denied"),  # denial only reachable from pending_approval
            ("dispatched", "succeeded"),  # must go through running
            ("dispatched", "pending"),  # reverse flow forbidden
            ("running", "dispatched"),  # reverse flow forbidden
        ],
    )
    def test_invalid_rejected(self, frm: TaskState, to: TaskState) -> None:
        t = _leaf(frm)
        with pytest.raises(ValueError, match="invalid transition|not in"):
            transition(t, to)


class TestTransitionPurity:
    def test_returns_new_instance(self) -> None:
        t = _leaf("pending")
        t2 = transition(t, "dispatched")
        assert t is not t2
        assert t.state == "pending"

    def test_failure_reason_and_dim_recorded(self) -> None:
        t = _leaf("running")
        t2 = transition(
            t, "failed", failure_reason="budget_exceeded", failure_dim="wall"
        )
        assert t2.state == "failed"
        assert t2.outcome == "failed"
        assert t2.failureReason == "budget_exceeded"
        assert t2.failureDim == "wall"


class TestTransitionTableSurface:
    def test_every_terminal_has_empty_edges(self) -> None:
        for terminal in TERMINAL_STATES:
            assert ALLOWED_TRANSITIONS.get(terminal) == frozenset()

    def test_table_keys_cover_all_states(self) -> None:
        expected = {
            "pending",
            "pending_approval",
            "dispatched",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "denied",
            "denied_by_timeout",
        }
        assert set(ALLOWED_TRANSITIONS.keys()) == expected
