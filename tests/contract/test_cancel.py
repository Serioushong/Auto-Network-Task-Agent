"""T013 — contract test for CancelMessage.

References `contracts/cancel-message.schema.json`. RED until T022.

Covers:
- Required fields (kind, traceId, userId, receivedAt).
- userId length overflow (≤ 128).
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.cancel import CancelMessage
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "cancel-message.schema.json"


def _valid() -> dict:
    return {
        "kind": "cancel_request",
        "traceId": "01J9TRACE000000000",
        "userId": "alice@local",
        "receivedAt": "2026-04-21T00:02:00Z",
    }


class TestValid:
    def test_pydantic(self) -> None:
        msg = CancelMessage.model_validate(_valid())
        assert msg.userId == "alice@local"

    def test_schema(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid())


class TestKindLocked:
    @pytest.mark.parametrize("bad", ["cancel", "CANCEL_REQUEST", "", "abort"])
    def test_wrong_kind_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            CancelMessage.model_validate(_valid() | {"kind": bad})


class TestRequired:
    @pytest.mark.parametrize(
        "field", ["kind", "traceId", "userId", "receivedAt"]
    )
    def test_missing_rejected(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        with pytest.raises(ValidationError):
            CancelMessage.model_validate(instance)
        assert_json_schema_rejects(SCHEMA, instance)


class TestUserIdLength:
    def test_boundary(self) -> None:
        CancelMessage.model_validate(_valid() | {"userId": "a" * 128})

    def test_over_128_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CancelMessage.model_validate(_valid() | {"userId": "a" * 129})


class TestTraceIdLength:
    @pytest.mark.parametrize("tid", ["short", "a" * 15, "a" * 65])
    def test_out_of_range_rejected(self, tid: str) -> None:
        with pytest.raises(ValidationError):
            CancelMessage.model_validate(_valid() | {"traceId": tid})

    @pytest.mark.parametrize("tid", ["a" * 16, "a" * 64])
    def test_boundary(self, tid: str) -> None:
        CancelMessage.model_validate(_valid() | {"traceId": tid})
