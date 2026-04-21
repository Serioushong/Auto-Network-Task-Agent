"""T017 — Task pydantic mirror of `contracts/task.schema.json`.

Covers the 9-state lifecycle (pending / pending_approval / dispatched / running
/ succeeded / failed / cancelled / denied / denied_by_timeout) and the
root_intent vs leaf_action dichotomy. Cross-field invariants encoded as a
`model_validator`:
- root_intent MUST NOT carry parentTaskId
- leaf_action MUST carry capability + riskLevel + budget
- terminal state <=> outcome present and equal to state
- failureReason=budget_exceeded <=> failureDim present
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .budget import Budget

TaskKind = Literal["root_intent", "leaf_action"]
RiskLevel = Literal["NORMAL", "HIGH_RISK"]
TaskState = Literal[
    "pending",
    "pending_approval",
    "dispatched",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "denied",
    "denied_by_timeout",
]
TaskOutcome = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "denied",
    "denied_by_timeout",
]
FailureReason = Literal[
    "no_capable_worker",
    "sandbox_limit",
    "budget_exceeded",
    "worker_crashed",
    "hard_terminated",
    "kernel_restart",
    "user_rejected",
    "approval_timeout",
]
FailureDim = Literal["wall", "tool", "token"]

TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {"succeeded", "failed", "cancelled", "denied", "denied_by_timeout"}
)
"""States after which no further state transitions are allowed (INV-2)."""


class Task(BaseModel):
    """Task tree node. Either the root_intent or a leaf_action."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=16, max_length=64)
    parentTaskId: str | None = Field(default=None, min_length=16, max_length=64)
    traceId: str = Field(min_length=16, max_length=64)
    kind: TaskKind
    capability: str | None = Field(default=None, min_length=1, max_length=64)
    riskLevel: RiskLevel | None = None
    budget: Budget | None = None
    state: TaskState
    outcome: TaskOutcome | None = None
    failureReason: FailureReason | None = None
    failureDim: FailureDim | None = None
    payload: dict[str, Any]
    resultHash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    assignedWorkerId: str | None = Field(default=None, min_length=1, max_length=128)
    createdAt: datetime
    dispatchedAt: datetime | None = None
    startedAt: datetime | None = None
    terminalAt: datetime | None = None

    @model_validator(mode="after")
    def _enforce_invariants(self) -> Task:
        if self.kind == "root_intent":
            if self.parentTaskId is not None:
                raise ValueError(
                    "root_intent MUST NOT carry parentTaskId (schema allOf[1])"
                )
        elif self.kind == "leaf_action":
            missing = [
                name
                for name, value in (
                    ("capability", self.capability),
                    ("riskLevel", self.riskLevel),
                    ("budget", self.budget),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"leaf_action requires fields {missing} (schema allOf[0])"
                )

        is_terminal = self.state in TERMINAL_STATES
        if is_terminal and self.outcome is None:
            raise ValueError(
                f"terminal state {self.state!r} requires outcome to be set"
            )
        if not is_terminal and self.outcome is not None:
            raise ValueError(
                f"non-terminal state {self.state!r} must not carry outcome"
            )
        if is_terminal and self.outcome != self.state:
            raise ValueError(
                f"terminal state {self.state!r} must match outcome={self.outcome!r}"
            )

        if self.failureReason == "budget_exceeded" and self.failureDim is None:
            raise ValueError(
                "failureReason=budget_exceeded requires failureDim (FR-018)"
            )
        if self.failureDim is not None and self.failureReason != "budget_exceeded":
            raise ValueError(
                "failureDim only valid when failureReason=budget_exceeded"
            )
        return self
