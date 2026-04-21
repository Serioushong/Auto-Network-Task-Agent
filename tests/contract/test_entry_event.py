"""T007 — contract test for EntryEvent.

References `specs/001-orchestrator-kernel/contracts/entry-event.schema.json`.
RED until T016 ships `orchestrator_kernel.contracts.entry_event.EntryEvent`.

Covers:
- Missing required fields.
- `text` body longer than 16_384 bytes (FR-031 hard cap).
- Illegal `sourceChannel` enum.
- `eventId` length out of the 16..64 range.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.entry_event import EntryEvent
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "entry-event.schema.json"


def _valid() -> dict:
    return {
        "eventId": "01J9ABCDXYZ1234567",
        "userId": "alice@local",
        "text": "帮我把桌面上的 notes.txt 备份到 D 盘",
        "sourceChannel": "cli",
        "receivedAt": "2026-04-21T00:00:00Z",
    }


class TestValid:
    def test_pydantic_accepts_minimum(self) -> None:
        event = EntryEvent.model_validate(_valid())
        assert event.eventId == "01J9ABCDXYZ1234567"
        assert event.sourceChannel == "cli"

    def test_pydantic_accepts_with_meta(self) -> None:
        instance = _valid() | {"meta": {"client_version": "1.0"}}
        assert EntryEvent.model_validate(instance).meta == {"client_version": "1.0"}

    def test_json_schema_accepts(self) -> None:
        assert_json_schema_accepts(SCHEMA, _valid())


class TestMissingRequired:
    @pytest.mark.parametrize(
        "field", ["eventId", "userId", "text", "sourceChannel", "receivedAt"]
    )
    def test_pydantic_rejects_missing(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        with pytest.raises(ValidationError) as exc:
            EntryEvent.model_validate(instance)
        assert field in str(exc.value)

    @pytest.mark.parametrize(
        "field", ["eventId", "userId", "text", "sourceChannel", "receivedAt"]
    )
    def test_schema_rejects_missing(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        assert_json_schema_rejects(SCHEMA, instance)


class TestPayloadSize:
    """FR-031: UTF-8 byte length <= 16 KB. 16385 bytes MUST be rejected."""

    def test_pydantic_rejects_16385_bytes(self) -> None:
        instance = _valid() | {"text": "a" * 16385}
        with pytest.raises(ValidationError):
            EntryEvent.model_validate(instance)

    def test_pydantic_accepts_exactly_16384_bytes(self) -> None:
        instance = _valid() | {"text": "a" * 16384}
        assert EntryEvent.model_validate(instance).text == "a" * 16384

    def test_pydantic_counts_utf8_bytes_not_chars(self) -> None:
        # 中文字每 3 bytes；5462 * 3 = 16386 > 16384 应该被拒
        instance = _valid() | {"text": "中" * 5462}
        with pytest.raises(ValidationError):
            EntryEvent.model_validate(instance)


class TestSourceChannelEnum:
    @pytest.mark.parametrize("bad", ["telegram", "SMS", "cli ", "", "wechat_bot"])
    def test_pydantic_rejects_unknown(self, bad: str) -> None:
        instance = _valid() | {"sourceChannel": bad}
        with pytest.raises(ValidationError):
            EntryEvent.model_validate(instance)

    @pytest.mark.parametrize("bad", ["telegram", "SMS", "", "wechat_bot"])
    def test_schema_rejects_unknown(self, bad: str) -> None:
        instance = _valid() | {"sourceChannel": bad}
        assert_json_schema_rejects(SCHEMA, instance)


class TestEventIdLength:
    @pytest.mark.parametrize("eid", ["short", "a" * 15, "a" * 65, "a" * 128])
    def test_pydantic_rejects_out_of_range(self, eid: str) -> None:
        instance = _valid() | {"eventId": eid}
        with pytest.raises(ValidationError):
            EntryEvent.model_validate(instance)

    @pytest.mark.parametrize("eid", ["a" * 16, "a" * 64])
    def test_pydantic_accepts_boundary(self, eid: str) -> None:
        instance = _valid() | {"eventId": eid}
        assert EntryEvent.model_validate(instance).eventId == eid
