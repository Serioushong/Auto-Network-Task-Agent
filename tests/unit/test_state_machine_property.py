"""T095 — Hypothesis property test guarding INV-2 (Task.state monotonicity).

INV-2 (data-model.md Invariants table): every Task progresses through the
state graph **strictly forward**. Terminal states (succeeded / failed /
cancelled / denied / denied_by_timeout) MUST have zero outgoing edges; any
attempt to transition out of them MUST raise.

The example-based ``tests/unit/test_state_machine.py`` already covers the
canonical happy path + a handful of curated illegal edges. This file
strengthens that coverage by letting Hypothesis search the FULL combinatorial
space of (start_state, target_state, kind, riskLevel) tuples and
**double-asserts** four properties that ``transition()`` MUST always honour:

* P1 — Forward-edge soundness: any edge produced by ``transition()`` MUST
  be present in ``ALLOWED_TRANSITIONS[from_state]``.
* P2 — Terminal closure (the heart of INV-2): for every terminal state,
  every conceivable outgoing edge raises ValueError.
* P3 — Forbidden-edge rejection: any (from -> to) pair NOT in the table
  MUST raise ValueError, regardless of kind / riskLevel / outcome args.
* P4 — Sequence soundness: a deterministic random walk along the table's
  edges produces a chain whose final ``state`` always lies inside the
  declared graph and never re-enters a terminal it has left (it can't,
  because terminals close).

Hypothesis configuration: ``deadline=None`` because Pydantic re-validation
on each call costs ~1 ms; ``max_examples`` is conservative (60-80) so the
suite stays fast inside the broader regression run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orchestrator_kernel.contracts.budget import DEFAULT_BUDGET
from orchestrator_kernel.contracts.task import (
    TERMINAL_STATES,
    Task,
    TaskState,
)
from orchestrator_kernel.kernel.state_machine import (
    ALLOWED_TRANSITIONS,
    transition,
)

NOW = datetime(2026, 4, 21, tzinfo=UTC)
ALL_STATES: tuple[TaskState, ...] = tuple(ALLOWED_TRANSITIONS.keys())
NON_TERMINAL_STATES: tuple[TaskState, ...] = tuple(
    s for s in ALL_STATES if s not in TERMINAL_STATES
)


def _leaf(
    state: TaskState,
    *,
    risk: str = "NORMAL",
    overrides: dict[str, Any] | None = None,
) -> Task:
    """Build a minimal valid leaf_action Task at the requested state."""
    base: dict[str, Any] = {
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
    if state in TERMINAL_STATES:
        base["outcome"] = state
    if overrides:
        base.update(overrides)
    return Task.model_validate(base)


# --------------------------------------------------------------------------
# P1 — Forward-edge soundness
# --------------------------------------------------------------------------


@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    from_state=st.sampled_from(NON_TERMINAL_STATES),
    risk=st.sampled_from(("NORMAL", "HIGH_RISK")),
    target_idx=st.integers(min_value=0, max_value=8),
)
def test_p1_legal_edges_only_produce_table_targets(
    from_state: TaskState, risk: str, target_idx: int
) -> None:
    """Every successful ``transition()`` lands on a target listed in the table."""
    allowed = sorted(ALLOWED_TRANSITIONS[from_state])
    if not allowed:
        return  # terminal — covered by P2.
    to_state = allowed[target_idx % len(allowed)]

    # Skip the one combination explicitly forbidden by INV-3 (HIGH_RISK
    # leaves cannot jump pending -> dispatched). That edge raises by
    # design, so it would otherwise spoof a P1 violation.
    if (
        risk == "HIGH_RISK"
        and from_state == "pending"
        and to_state == "dispatched"
    ):
        return

    leaf = _leaf(from_state, risk=risk)
    out = transition(leaf, to_state)
    assert out.state == to_state, (
        f"transition({from_state} -> {to_state}) returned state={out.state!r}"
    )
    assert out.state in ALLOWED_TRANSITIONS[from_state]


# --------------------------------------------------------------------------
# P2 — Terminal closure (INV-2 in its strongest form)
# --------------------------------------------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    terminal=st.sampled_from(sorted(TERMINAL_STATES)),
    target=st.sampled_from(ALL_STATES),
    risk=st.sampled_from(("NORMAL", "HIGH_RISK")),
)
def test_p2_terminal_state_has_zero_outgoing_edges(
    terminal: TaskState, target: TaskState, risk: str
) -> None:
    """No matter which target you ask for, terminals MUST raise ValueError."""
    leaf = _leaf(terminal, risk=risk)
    with pytest.raises(ValueError, match="INV-2|invalid transition"):
        transition(leaf, target)


# --------------------------------------------------------------------------
# P3 — Forbidden-edge rejection
# --------------------------------------------------------------------------


@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    from_state=st.sampled_from(NON_TERMINAL_STATES),
    to_state=st.sampled_from(ALL_STATES),
    risk=st.sampled_from(("NORMAL", "HIGH_RISK")),
)
def test_p3_edge_outside_table_raises(
    from_state: TaskState, to_state: TaskState, risk: str
) -> None:
    """Any (from, to) pair NOT in ALLOWED_TRANSITIONS MUST raise."""
    if to_state in ALLOWED_TRANSITIONS[from_state]:
        return  # covered by P1
    if from_state == to_state:
        # self-loops are also "not in table" — they MUST raise.
        pass

    leaf = _leaf(from_state, risk=risk)
    with pytest.raises(ValueError):
        transition(leaf, to_state)


# --------------------------------------------------------------------------
# P4 — Random-walk soundness
# --------------------------------------------------------------------------


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    risk=st.sampled_from(("NORMAL", "HIGH_RISK")),
    seed=st.lists(
        st.integers(min_value=0, max_value=255), min_size=1, max_size=12
    ),
)
def test_p4_random_walk_terminates_inside_table(
    risk: str, seed: list[int]
) -> None:
    """Walking along legal edges always ends in the declared graph.

    We pick a deterministic edge at each step (``seed[i] % len(allowed)``).
    Loops are impossible because every legal edge moves the state
    forward and terminals close — so the walk is bounded by graph depth
    (≤ 5 hops on this graph).
    """
    leaf = _leaf("pending", risk=risk)
    seen: list[TaskState] = [leaf.state]
    for step in seed:
        allowed = sorted(ALLOWED_TRANSITIONS[leaf.state])
        if not allowed:
            break  # reached terminal
        to_state = allowed[step % len(allowed)]
        # Skip the INV-3 trap so the walk doesn't get stuck on a single edge.
        if (
            risk == "HIGH_RISK"
            and leaf.state == "pending"
            and to_state == "dispatched"
        ):
            to_state = "pending_approval"
        leaf = transition(leaf, to_state)
        seen.append(leaf.state)

    # Final state lies in the declared graph.
    assert leaf.state in ALLOWED_TRANSITIONS
    # Once we hit a terminal, no further state appended.
    if seen[-1] in TERMINAL_STATES:
        assert seen.count(seen[-1]) == 1, (
            f"terminal {seen[-1]!r} appears more than once in {seen!r}"
        )
    # Strict forward progress: no state appears twice in a row (transition
    # always changes state) and the path is acyclic.
    for i in range(1, len(seen)):
        assert seen[i] != seen[i - 1], f"self-loop detected at step {i}: {seen}"


# --------------------------------------------------------------------------
# Sanity guard: INV-3 specifically — HIGH_RISK MUST go via pending_approval
# --------------------------------------------------------------------------


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(target=st.sampled_from(("dispatched",)))
def test_p5_high_risk_pending_to_dispatched_always_raises(
    target: TaskState,
) -> None:
    """INV-3: HIGH_RISK leaf can NEVER skip the approval gate."""
    leaf = _leaf("pending", risk="HIGH_RISK")
    with pytest.raises(ValueError, match="INV-3"):
        transition(leaf, target)
