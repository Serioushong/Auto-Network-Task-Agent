"""T029 — Task state machine (INV-2 / INV-3 / FR-017).

Pure function `transition(task, to_state, ...) -> Task` that returns a new,
re-validated Task instance with the state advanced. Never mutates its input.

The transition table is one-way: every terminal state has zero outgoing edges,
every reverse flow (running -> dispatched, dispatched -> pending, ...) is
rejected. HIGH_RISK leaf_action tasks MUST pass through `pending_approval`
before being marked `dispatched` (INV-3).

This module is deliberately the only place in the kernel allowed to construct
a Task with a different state than the one it received — all state-changing
call sites route through `transition()` so INV-2 / INV-3 violations become
impossible to express.
"""

from __future__ import annotations

from typing import Any

from ..contracts.task import (
    TERMINAL_STATES,
    FailureDim,
    FailureReason,
    Task,
    TaskOutcome,
    TaskState,
)

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    "pending": frozenset({"pending_approval", "dispatched", "failed", "cancelled"}),
    "pending_approval": frozenset(
        {"dispatched", "denied", "denied_by_timeout", "cancelled"}
    ),
    "dispatched": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    # Terminal states: zero outgoing edges (INV-2).
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "denied": frozenset(),
    "denied_by_timeout": frozenset(),
}
"""Machine-readable state graph. Any edge outside this table raises."""


def transition(
    task: Task,
    to_state: TaskState,
    *,
    outcome: TaskOutcome | None = None,
    failure_reason: FailureReason | None = None,
    failure_dim: FailureDim | None = None,
) -> Task:
    """Advance `task.state` to `to_state`, returning a new validated Task.

    Args:
        task: The Task to transition (not mutated).
        to_state: Target state; must be reachable from `task.state`.
        outcome: Terminal outcome override. Defaults to `to_state` itself
            when transitioning into a terminal state (`succeeded` -> outcome
            `succeeded`, etc.).
        failure_reason: Optional FailureReason; required when `to_state=failed`
            and caller knows the cause. Not auto-populated.
        failure_dim: Only valid when `failure_reason=budget_exceeded`.

    Raises:
        ValueError: INV-2 (outgoing edge from terminal state),
            INV-3 (HIGH_RISK bypassing pending_approval), or illegal edge.
    """
    if task.state in TERMINAL_STATES:
        raise ValueError(
            f"INV-2 violation: terminal state {task.state!r} has no outgoing edges "
            f"(attempted transition to {to_state!r})"
        )

    allowed = ALLOWED_TRANSITIONS.get(task.state, frozenset())
    if to_state not in allowed:
        raise ValueError(
            f"invalid transition: {task.state!r} -> {to_state!r} "
            f"not in allowed set {sorted(allowed)}"
        )

    if (
        task.kind == "leaf_action"
        and task.riskLevel == "HIGH_RISK"
        and task.state == "pending"
        and to_state == "dispatched"
    ):
        raise ValueError(
            "INV-3 violation: HIGH_RISK leaf_action MUST pass through "
            "pending_approval before dispatched"
        )

    updates: dict[str, Any] = {"state": to_state}
    if to_state in TERMINAL_STATES:
        updates["outcome"] = outcome if outcome is not None else to_state
    if failure_reason is not None:
        updates["failureReason"] = failure_reason
    if failure_dim is not None:
        updates["failureDim"] = failure_dim

    # Round-trip through model_validate so the Task @model_validator re-runs
    # all cross-field invariants (outcome/state parity, failureDim gating, ...).
    dumped = task.model_dump(mode="json") | updates
    return Task.model_validate(dumped)
