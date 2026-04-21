"""T010 — contract test for WorkerRegistration.

References `contracts/worker-registration.schema.json`. RED until T019.

Covers:
- capability name pattern (`^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$`).
- riskLevel enum.
- resourceLimits ranges.
- capability.budget over system hard cap rejected at registration time.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.worker import (
    Capability,
    ResourceLimits,
    WorkerRegistration,
)
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "worker-registration.schema.json"


def _valid_cap() -> dict:
    return {
        "name": "echo.say",
        "riskLevel": "NORMAL",
        "budget": {
            "wall_clock_ms": 60_000,
            "max_tool_calls": 10,
            "max_tokens": 20_000,
        },
    }


def _valid() -> dict:
    return {
        "workerId": "echo-worker-01",
        "pid": 12345,
        "capabilities": [_valid_cap()],
        "resourceLimits": {"memory_mb": 256, "cpu_pct": 100, "wall_clock_ms": 60_000},
    }


class TestValid:
    def test_pydantic_accepts(self) -> None:
        reg = WorkerRegistration.model_validate(_valid())
        assert reg.workerId == "echo-worker-01"
        assert len(reg.capabilities) == 1

    def test_schema_accepts(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid())


class TestCapabilityNamePattern:
    @pytest.mark.parametrize(
        "name", ["echo.say", "file.delete", "web.search", "a.b.c", "long_name.sub_op"]
    )
    def test_valid_names(self, name: str) -> None:
        cap = _valid_cap() | {"name": name}
        Capability.model_validate(cap)

    @pytest.mark.parametrize(
        "name",
        [
            "echo",
            "Echo.Say",
            ".echo.say",
            "echo.",
            "1echo.say",
            "echo-say",
            "echo. say",
            "",
            "a" * 65,
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        with pytest.raises(ValidationError):
            Capability.model_validate(_valid_cap() | {"name": name})


class TestRiskLevelEnum:
    @pytest.mark.parametrize("rl", ["NORMAL", "HIGH_RISK"])
    def test_valid(self, rl: str) -> None:
        Capability.model_validate(_valid_cap() | {"riskLevel": rl})

    @pytest.mark.parametrize("rl", ["normal", "high_risk", "MEDIUM", "", "CRITICAL"])
    def test_invalid(self, rl: str) -> None:
        with pytest.raises(ValidationError):
            Capability.model_validate(_valid_cap() | {"riskLevel": rl})


class TestResourceLimits:
    @pytest.mark.parametrize(
        "field,bad",
        [
            ("memory_mb", 15),
            ("memory_mb", 8_193),
            ("cpu_pct", 0),
            ("cpu_pct", 401),
            ("wall_clock_ms", 0),
            ("wall_clock_ms", 1_800_001),
        ],
    )
    def test_out_of_range_rejected(self, field: str, bad: int) -> None:
        rl = {"memory_mb": 256, "cpu_pct": 100, "wall_clock_ms": 60_000} | {field: bad}
        with pytest.raises(ValidationError):
            ResourceLimits.model_validate(rl)


class TestCapabilityBudgetHardCap:
    """Worker registration MUST reject any capability whose budget exceeds hard cap.

    This is FR-018 + spec.md Q2 enforcement at the 'edge' (registration time).
    """

    @pytest.mark.parametrize(
        "field,bad",
        [
            ("wall_clock_ms", 1_800_001),
            ("max_tool_calls", 201),
            ("max_tokens", 500_001),
        ],
    )
    def test_rejects_over_hard_cap(self, field: str, bad: int) -> None:
        cap = _valid_cap()
        cap["budget"] = cap["budget"] | {field: bad}
        reg = _valid() | {"capabilities": [cap]}
        with pytest.raises(ValidationError):
            WorkerRegistration.model_validate(reg)


class TestMissingRequired:
    @pytest.mark.parametrize(
        "field", ["workerId", "pid", "capabilities", "resourceLimits"]
    )
    def test_top_level(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        with pytest.raises(ValidationError):
            WorkerRegistration.model_validate(instance)
        assert_json_schema_rejects(SCHEMA, instance)

    def test_capabilities_must_have_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            WorkerRegistration.model_validate(_valid() | {"capabilities": []})
