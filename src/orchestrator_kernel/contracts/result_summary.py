"""T024 — ResultSummary pydantic mirror of `contracts/result-summary.schema.json`.

Kernel -> User final-state push via the original sourceChannel (FR-029 / FR-030).
When `traceOutcome=kernel_restarted`, the `message` field MUST include
're-submit' AND 'NEW eventId' substrings; this is a schema-level contract and
is enforced here by a `model_validator`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LeafOutcome = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "denied",
    "denied_by_timeout",
]

TraceOutcome = Literal[
    "all_succeeded",
    "partial_failed",
    "all_failed",
    "cancelled",
    "denied",
    "rejected",
    "kernel_restarted",
]


class LeafResult(BaseModel):
    """Per-leaf rollup included in the ResultSummary.leafResults array."""

    model_config = ConfigDict(extra="forbid")

    taskId: str = Field(min_length=16, max_length=64)
    capability: str = Field(max_length=64)
    outcome: LeafOutcome
    failureReason: str | None = Field(default=None, max_length=64)
    resultHash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ResultSummary(BaseModel):
    """Final push delivered via the sourceChannel that accepted the EntryEvent."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["result_summary"]
    traceId: str = Field(min_length=16, max_length=64)
    eventId: str = Field(min_length=16, max_length=64)
    userId: str = Field(max_length=128)
    commandDigest: str = Field(max_length=256)
    traceOutcome: TraceOutcome
    leafResults: list[LeafResult]
    message: str = Field(max_length=2048)
    preparedAt: datetime
    deliveryAttempt: int = Field(ge=0, le=3)

    @model_validator(mode="after")
    def _enforce_restart_hint(self) -> ResultSummary:
        if self.traceOutcome == "kernel_restarted":
            needle_a = "re-submit"
            needle_b = "NEW eventId"
            if needle_a not in self.message or needle_b not in self.message:
                raise ValueError(
                    "traceOutcome=kernel_restarted requires message to contain "
                    f"{needle_a!r} and {needle_b!r} (FR-029 Q4)"
                )
        return self
