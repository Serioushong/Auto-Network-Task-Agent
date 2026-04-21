"""T018 — Budget pydantic mirror of `contracts/budget.schema.json`.

Three-dimensional per-task budget (FR-018). The system-wide hard caps are baked
into the type via pydantic Field constraints so any cross-boundary message that
carries a Budget can never exceed them. No runtime config knob relaxes these.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Budget(BaseModel):
    """Frozen, forbid-extra three-dim budget with hard caps enforced at parse time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_clock_ms: int = Field(ge=1, le=1_800_000)
    max_tool_calls: int = Field(ge=1, le=200)
    max_tokens: int = Field(ge=1, le=500_000)


SYSTEM_HARD_CAP: Budget = Budget(
    wall_clock_ms=1_800_000,
    max_tool_calls=200,
    max_tokens=500_000,
)
"""Upper-bound envelope. Worker-registered capability budgets MUST be <= this."""


DEFAULT_BUDGET: Budget = Budget(
    wall_clock_ms=60_000,
    max_tool_calls=10,
    max_tokens=20_000,
)
"""Conservative per-spec Clarification Q2 fallback when capability omits a dim."""
