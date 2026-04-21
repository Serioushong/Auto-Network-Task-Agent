"""T008 — contract test for Task.

References `contracts/task.schema.json`. RED until T017.

Covers:
- root_intent vs leaf_action branching.
- leaf missing capability / riskLevel / budget rejected.
- state/outcome cross-consistency.
- resultHash pattern.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.task import Task
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "task.schema.json"


def _valid_root() -> dict:
    return {
        "taskId": "01J9ROOT0000000000",
        "parentTaskId": None,
        "traceId": "01J9TRACE000000000",
        "kind": "root_intent",
        "state": "pending",
        "payload": {"intent_summary": "backup desktop file"},
        "createdAt": "2026-04-21T00:00:00Z",
    }


def _valid_leaf() -> dict:
    return {
        "taskId": "01J9LEAF0000000000",
        "parentTaskId": "01J9ROOT0000000000",
        "traceId": "01J9TRACE000000000",
        "kind": "leaf_action",
        "capability": "echo.say",
        "riskLevel": "NORMAL",
        "budget": {"wall_clock_ms": 60_000, "max_tool_calls": 10, "max_tokens": 20_000},
        "state": "pending",
        "payload": {"message": "hello"},
        "createdAt": "2026-04-21T00:00:00Z",
    }


class TestRootAccepts:
    def test_pydantic_accepts(self) -> None:
        task = Task.model_validate(_valid_root())
        assert task.kind == "root_intent"
        assert task.parentTaskId is None

    def test_schema_accepts(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid_root())


class TestLeafAccepts:
    def test_pydantic_accepts(self) -> None:
        task = Task.model_validate(_valid_leaf())
        assert task.kind == "leaf_action"
        assert task.capability == "echo.say"

    def test_schema_accepts(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid_leaf())


class TestLeafMissingRequired:
    @pytest.mark.parametrize("field", ["capability", "riskLevel", "budget"])
    def test_pydantic_rejects(self, field: str) -> None:
        instance = _valid_leaf()
        del instance[field]
        with pytest.raises(ValidationError):
            Task.model_validate(instance)


class TestStateEnum:
    VALID_STATES = [
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

    @pytest.mark.parametrize("st", VALID_STATES)
    def test_all_valid_accepted(self, st: str) -> None:
        instance = _valid_leaf() | {"state": st}
        if st in {"succeeded", "failed", "cancelled", "denied", "denied_by_timeout"}:
            instance["outcome"] = st
        Task.model_validate(instance)

    @pytest.mark.parametrize("bad", ["queued", "done", "", "SUCCEEDED", "PENDING"])
    def test_invalid_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            Task.model_validate(_valid_leaf() | {"state": bad})


class TestOutcomeCrossConsistency:
    def test_outcome_forbidden_on_nonterminal(self) -> None:
        # pending + outcome=succeeded is incoherent; model_validator MUST reject.
        with pytest.raises(ValidationError):
            Task.model_validate(
                _valid_leaf() | {"state": "pending", "outcome": "succeeded"}
            )

    def test_outcome_required_on_terminal(self) -> None:
        # succeeded state without outcome is incoherent.
        with pytest.raises(ValidationError):
            Task.model_validate(_valid_leaf() | {"state": "succeeded"})

    def test_outcome_matches_terminal_state(self) -> None:
        # state=failed but outcome=succeeded is incoherent.
        with pytest.raises(ValidationError):
            Task.model_validate(
                _valid_leaf() | {"state": "failed", "outcome": "succeeded"}
            )


class TestResultHashPattern:
    def test_valid_32_hex_lower(self) -> None:
        ok = "0123456789abcdef0123456789abcdef"
        Task.model_validate(
            _valid_leaf()
            | {"state": "succeeded", "outcome": "succeeded", "resultHash": ok}
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "0123",
            "GGGG56789abcdef0123456789abcdef0",
            "0123456789ABCDEF0123456789ABCDEF",
            "0123456789abcdef0123456789abcdef0",
        ],
    )
    def test_invalid_hash_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            Task.model_validate(
                _valid_leaf()
                | {"state": "succeeded", "outcome": "succeeded", "resultHash": bad}
            )


class TestRootInvariant:
    def test_root_with_parent_rejected_by_schema(self) -> None:
        bad = _valid_root() | {"parentTaskId": "01J9PARENT000000000"}
        assert_json_schema_rejects(SCHEMA, bad)

    def test_root_with_parent_rejected_by_pydantic(self) -> None:
        bad = _valid_root() | {"parentTaskId": "01J9PARENT000000000"}
        with pytest.raises(ValidationError):
            Task.model_validate(bad)
