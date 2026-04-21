"""T015 — contract test for ResultSummary.

References `contracts/result-summary.schema.json`. RED until T024.

Covers:
- traceOutcome enum.
- leafResults items sub-schema.
- deliveryAttempt ≤ 3.
- kernel_restarted => message MUST contain the hint to re-submit with a new eventId.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.result_summary import LeafResult, ResultSummary
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "result-summary.schema.json"


def _leaf() -> dict:
    return {
        "taskId": "01J9LEAF0000000000",
        "capability": "echo.say",
        "outcome": "succeeded",
        "resultHash": "0123456789abcdef0123456789abcdef",
    }


def _valid() -> dict:
    return {
        "kind": "result_summary",
        "traceId": "01J9TRACE000000000",
        "eventId": "01J9EVENT000000000",
        "userId": "alice@local",
        "commandDigest": "backup desktop notes.txt -> D:",
        "traceOutcome": "all_succeeded",
        "leafResults": [_leaf()],
        "message": "All 1 task succeeded. Latency 42ms.",
        "preparedAt": "2026-04-21T00:00:10Z",
        "deliveryAttempt": 0,
    }


class TestValid:
    def test_pydantic(self) -> None:
        rs = ResultSummary.model_validate(_valid())
        assert rs.traceOutcome == "all_succeeded"
        assert rs.deliveryAttempt == 0
        assert len(rs.leafResults) == 1

    def test_schema(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid())

    def test_leaf_result_standalone(self) -> None:
        assert LeafResult.model_validate(_leaf()).capability == "echo.say"


class TestTraceOutcomeEnum:
    @pytest.mark.parametrize(
        "o",
        [
            "all_succeeded",
            "partial_failed",
            "all_failed",
            "cancelled",
            "denied",
            "rejected",
            "kernel_restarted",
        ],
    )
    def test_valid(self, o: str) -> None:
        instance = _valid() | {"traceOutcome": o}
        if o == "kernel_restarted":
            instance["message"] = (
                "system interrupted; re-submit with a NEW eventId please."
            )
        ResultSummary.model_validate(instance)

    @pytest.mark.parametrize(
        "bad", ["succeeded", "failed", "SUCCEEDED", "restart", ""]
    )
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ResultSummary.model_validate(_valid() | {"traceOutcome": bad})


class TestDeliveryAttemptBounds:
    @pytest.mark.parametrize("n", [0, 1, 2, 3])
    def test_valid(self, n: int) -> None:
        ResultSummary.model_validate(_valid() | {"deliveryAttempt": n})

    @pytest.mark.parametrize("bad", [-1, 4, 10])
    def test_invalid(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            ResultSummary.model_validate(_valid() | {"deliveryAttempt": bad})


class TestKernelRestartedMessage:
    """FR-029 / schema: `kernel_restarted` MUST include 're-submit...NEW eventId'."""

    def test_hint_present_ok(self) -> None:
        instance = _valid() | {
            "traceOutcome": "kernel_restarted",
            "message": "system interrupted; re-submit with a NEW eventId please.",
            "leafResults": [],
        }
        rs = ResultSummary.model_validate(instance)
        assert rs.traceOutcome == "kernel_restarted"

    def test_hint_missing_rejected(self) -> None:
        instance = _valid() | {
            "traceOutcome": "kernel_restarted",
            "message": "Something happened.",
            "leafResults": [],
        }
        with pytest.raises(ValidationError):
            ResultSummary.model_validate(instance)


class TestLeafResultItemSchema:
    def test_leaf_missing_required(self) -> None:
        for field in ["taskId", "capability", "outcome"]:
            bad = _leaf()
            del bad[field]
            with pytest.raises(ValidationError):
                LeafResult.model_validate(bad)

    @pytest.mark.parametrize(
        "bad_outcome", ["succeeded_", "", "SUCCEEDED", "running"]
    )
    def test_leaf_outcome_enum(self, bad_outcome: str) -> None:
        with pytest.raises(ValidationError):
            LeafResult.model_validate(_leaf() | {"outcome": bad_outcome})

    @pytest.mark.parametrize(
        "bad_hash",
        ["0123", "G" * 32, "0123456789abcdef0123456789ABCDEF", "0" * 33],
    )
    def test_leaf_result_hash_pattern(self, bad_hash: str) -> None:
        with pytest.raises(ValidationError):
            LeafResult.model_validate(_leaf() | {"resultHash": bad_hash})


class TestRejectedEmptyLeafList:
    def test_rejected_outcome_with_empty_leaves_is_ok(self) -> None:
        instance = _valid() | {"traceOutcome": "rejected", "leafResults": []}
        ResultSummary.model_validate(instance)

    def test_schema_accepts_empty_leaves(self) -> None:
        instance = _valid() | {"traceOutcome": "rejected", "leafResults": []}
        assert_json_schema_accepts(SCHEMA, instance)


class TestMissingRequired:
    @pytest.mark.parametrize(
        "field",
        [
            "kind",
            "traceId",
            "eventId",
            "userId",
            "commandDigest",
            "traceOutcome",
            "leafResults",
            "message",
            "preparedAt",
            "deliveryAttempt",
        ],
    )
    def test_rejected(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        with pytest.raises(ValidationError):
            ResultSummary.model_validate(instance)
        assert_json_schema_rejects(SCHEMA, instance)
