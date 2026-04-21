"""T012 — contract test for ApprovalMessage (request + response union).

References `contracts/approval-message.schema.json`. RED until T021.

Covers:
- Both branches of the oneOf (approval_request / approval_response).
- userId length constraint (≤ 128).
- decision enum (approve / deny).
- expiresAt required on request.
- riskLevel must be HIGH_RISK on request (FR-010).
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.approval import (
    ApprovalMessage,
    ApprovalRequest,
    ApprovalResponse,
)
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "approval-message.schema.json"


def _valid_request() -> dict:
    return {
        "kind": "approval_request",
        "traceId": "01J9TRACE000000000",
        "taskId": "01J9LEAF0000000000",
        "capability": "file.delete",
        "riskLevel": "HIGH_RISK",
        "summary": "delete D:\\tmp\\old.log",
        "expiresAt": "2026-04-21T00:10:00Z",
    }


def _valid_response() -> dict:
    return {
        "kind": "approval_response",
        "traceId": "01J9TRACE000000000",
        "decision": "approve",
        "userId": "alice@local",
        "receivedAt": "2026-04-21T00:02:00Z",
    }


class TestValid:
    def test_request_accepted(self) -> None:
        msg = ApprovalMessage.validate_python(_valid_request())
        assert isinstance(msg, ApprovalRequest)

    def test_response_accepted(self) -> None:
        msg = ApprovalMessage.validate_python(_valid_response())
        assert isinstance(msg, ApprovalResponse)

    def test_request_schema(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid_request())

    def test_response_schema(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid_response())


class TestRequestRiskLevelLocked:
    def test_rejects_NORMAL(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(
                _valid_request() | {"riskLevel": "NORMAL"}
            )

    def test_rejects_arbitrary(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(
                _valid_request() | {"riskLevel": "MEDIUM"}
            )


class TestRequestRequired:
    @pytest.mark.parametrize(
        "field",
        ["traceId", "taskId", "capability", "riskLevel", "summary", "expiresAt"],
    )
    def test_missing_rejected(self, field: str) -> None:
        instance = _valid_request()
        del instance[field]
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(instance)
        assert_json_schema_rejects(SCHEMA, instance)


class TestResponseDecisionEnum:
    @pytest.mark.parametrize("d", ["approve", "deny"])
    def test_valid(self, d: str) -> None:
        ApprovalMessage.validate_python(_valid_response() | {"decision": d})

    @pytest.mark.parametrize("d", ["approved", "rejected", "", "APPROVE", "maybe"])
    def test_invalid(self, d: str) -> None:
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(_valid_response() | {"decision": d})


class TestResponseUserIdLength:
    def test_boundary_accepted(self) -> None:
        ApprovalMessage.validate_python(_valid_response() | {"userId": "a" * 128})

    def test_over_128_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(_valid_response() | {"userId": "a" * 129})


class TestRequestSummaryLength:
    def test_boundary_accepted(self) -> None:
        ApprovalMessage.validate_python(_valid_request() | {"summary": "x" * 512})

    def test_over_512_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalMessage.validate_python(_valid_request() | {"summary": "x" * 513})
