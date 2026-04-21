"""T009 — contract test for Budget.

References `contracts/budget.schema.json`. RED until T018.

Covers:
- Three-dimensional min=1 and system hard caps (1_800_000 / 200 / 500_000).
- Overflow rejected.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.budget import (
    DEFAULT_BUDGET,
    SYSTEM_HARD_CAP,
    Budget,
)
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "budget.schema.json"


def _valid() -> dict:
    return {"wall_clock_ms": 60_000, "max_tool_calls": 10, "max_tokens": 20_000}


class TestValid:
    def test_accepts_typical(self) -> None:
        b = Budget.model_validate(_valid())
        assert b.wall_clock_ms == 60_000

    def test_schema_accepts(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid())

    def test_accepts_hard_cap_exact(self) -> None:
        b = Budget.model_validate(
            {"wall_clock_ms": 1_800_000, "max_tool_calls": 200, "max_tokens": 500_000}
        )
        assert b.max_tokens == 500_000

    def test_accepts_minimum(self) -> None:
        b = Budget.model_validate(
            {"wall_clock_ms": 1, "max_tool_calls": 1, "max_tokens": 1}
        )
        assert b.wall_clock_ms == 1


class TestOverflow:
    @pytest.mark.parametrize(
        "field,bad",
        [
            ("wall_clock_ms", 1_800_001),
            ("wall_clock_ms", 10_000_000),
            ("max_tool_calls", 201),
            ("max_tool_calls", 1_000),
            ("max_tokens", 500_001),
            ("max_tokens", 1_000_000),
        ],
    )
    def test_pydantic_rejects_over_hard_cap(self, field: str, bad: int) -> None:
        instance = _valid() | {field: bad}
        with pytest.raises(ValidationError):
            Budget.model_validate(instance)

    @pytest.mark.parametrize(
        "field,bad",
        [("wall_clock_ms", 0), ("max_tool_calls", 0), ("max_tokens", 0)],
    )
    def test_pydantic_rejects_below_min(self, field: str, bad: int) -> None:
        instance = _valid() | {field: bad}
        with pytest.raises(ValidationError):
            Budget.model_validate(instance)

    @pytest.mark.parametrize(
        "field,bad",
        [("wall_clock_ms", 1_800_001), ("max_tool_calls", 201), ("max_tokens", 500_001)],
    )
    def test_schema_rejects_over_hard_cap(self, field: str, bad: int) -> None:
        instance = _valid() | {field: bad}
        assert_json_schema_rejects(SCHEMA, instance)


class TestConstants:
    """T018 MUST export DEFAULT_BUDGET and SYSTEM_HARD_CAP; referenced across kernel."""

    def test_default_budget_is_budget_instance(self) -> None:
        assert isinstance(DEFAULT_BUDGET, Budget)

    def test_default_budget_within_hard_cap(self) -> None:
        assert DEFAULT_BUDGET.wall_clock_ms <= SYSTEM_HARD_CAP.wall_clock_ms
        assert DEFAULT_BUDGET.max_tool_calls <= SYSTEM_HARD_CAP.max_tool_calls
        assert DEFAULT_BUDGET.max_tokens <= SYSTEM_HARD_CAP.max_tokens

    def test_system_hard_cap_matches_schema(self) -> None:
        assert SYSTEM_HARD_CAP.wall_clock_ms == 1_800_000
        assert SYSTEM_HARD_CAP.max_tool_calls == 200
        assert SYSTEM_HARD_CAP.max_tokens == 500_000

    def test_conservative_defaults_per_spec_q2(self) -> None:
        # spec.md Clarifications Q2: fallback wall 60s / 10 calls / 20k tokens
        assert DEFAULT_BUDGET.wall_clock_ms == 60_000
        assert DEFAULT_BUDGET.max_tool_calls == 10
        assert DEFAULT_BUDGET.max_tokens == 20_000
