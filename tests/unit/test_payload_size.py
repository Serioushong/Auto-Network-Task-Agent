"""T032 — failing unit test for payload-size guard.

RED until T033 ships `orchestrator_kernel.kernel.validators.assert_payload_size`.

FR-031: the `text` field in an EntryEvent MUST NOT exceed 16_384 UTF-8 bytes.
The check MUST run BEFORE any other validation so that the kernel rejects
oversize payloads cheaply. On rejection the guard emits an `AuditEvent` of
type `event_rejected_too_large` carrying the measured byte count.

The entry pipeline treats oversize as a hard-fail without creating a Trace;
the caller gets a `PayloadTooLarge` exception to forward to the audit writer
and the source channel's error response.
"""

from __future__ import annotations

import pytest

from orchestrator_kernel.kernel.validators import (
    DEFAULT_PAYLOAD_MAX_BYTES,
    PayloadTooLarge,
    assert_payload_size,
    build_too_large_audit,
)


def _raw_entry(text: str) -> dict[str, object]:
    return {
        "eventId": "01J9ABCDXYZ1234567",
        "userId": "alice",
        "text": text,
        "sourceChannel": "cli",
        "receivedAt": "2026-04-21T00:00:00Z",
    }


class TestBoundaries:
    def test_exactly_16384_bytes_ok(self) -> None:
        assert_payload_size(_raw_entry("a" * 16_384))

    def test_16385_bytes_rejected(self) -> None:
        with pytest.raises(PayloadTooLarge) as exc:
            assert_payload_size(_raw_entry("a" * 16_385))
        assert exc.value.actual_bytes == 16_385
        assert exc.value.limit_bytes == DEFAULT_PAYLOAD_MAX_BYTES

    def test_chinese_counted_by_bytes(self) -> None:
        # 5_462 中文 chars * 3 bytes/char = 16_386 bytes
        with pytest.raises(PayloadTooLarge) as exc:
            assert_payload_size(_raw_entry("中" * 5_462))
        assert exc.value.actual_bytes == 16_386


class TestCustomLimit:
    def test_override_limit(self) -> None:
        with pytest.raises(PayloadTooLarge) as exc:
            assert_payload_size(_raw_entry("a" * 100), limit_bytes=50)
        assert exc.value.limit_bytes == 50
        assert exc.value.actual_bytes == 100


class TestNoText:
    def test_missing_text_raises(self) -> None:
        bad: dict[str, object] = {
            "eventId": "01J9ABCDXYZ1234567",
            "userId": "alice",
            "sourceChannel": "cli",
            "receivedAt": "2026-04-21T00:00:00Z",
        }
        with pytest.raises(PayloadTooLarge):
            # Missing text field means we cannot trust the payload; treat as
            # malformed. (Schema layer catches this too; the guard produces
            # the same rejection class so callers have one exception contract.)
            assert_payload_size(bad)


class TestAuditEventShape:
    """`build_too_large_audit` returns a well-formed AuditEvent pydantic instance."""

    def test_shape(self) -> None:
        with pytest.raises(PayloadTooLarge) as exc:
            assert_payload_size(_raw_entry("a" * 17_000))
        ev = build_too_large_audit(exc.value, user_id="alice")
        assert ev.eventType == "event_rejected_too_large"
        assert ev.actor == "user:alice"
        assert ev.outcome == "rejected"
        assert ev.extra is not None
        assert ev.extra["actual_bytes"] == 17_000
        assert ev.extra["limit_bytes"] == DEFAULT_PAYLOAD_MAX_BYTES
        assert ev.traceId is None

    def test_actor_fallback_when_userid_invalid(self) -> None:
        # If the raw userId is not actor-pattern-safe (contains '@' etc.), the
        # audit MUST still land; fallback to actor='system' and store the
        # offending userId in extra.
        with pytest.raises(PayloadTooLarge) as exc:
            assert_payload_size(_raw_entry("a" * 17_000))
        ev = build_too_large_audit(exc.value, user_id="alice@bad")
        assert ev.actor == "system"
        assert ev.extra is not None
        assert ev.extra["raw_user_id"] == "alice@bad"
