"""T033 — payload-size guard, runs before any other validation (FR-031).

We reject oversize text bodies at the earliest possible point so the kernel
never loads, parses, or logs a pathologically large string. The fallback
audit event (`event_rejected_too_large`) carries the measured byte count but
never the original content.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..contracts.audit import AuditEvent
from ..contracts.entry_event import TEXT_BYTE_CAP

DEFAULT_PAYLOAD_MAX_BYTES: int = TEXT_BYTE_CAP
"""Same 16 KB number defined on the contract. Config can only lower this."""


class PayloadTooLarge(Exception):
    """Raised when the entry text exceeds the allowed UTF-8 byte length."""

    def __init__(
        self,
        *,
        actual_bytes: int,
        limit_bytes: int,
        user_id_raw: str | None = None,
    ) -> None:
        super().__init__(
            f"payload too large: {actual_bytes} > {limit_bytes} bytes (FR-031)"
        )
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes
        self.user_id_raw = user_id_raw


def assert_payload_size(
    raw_event: dict[str, Any],
    *,
    limit_bytes: int = DEFAULT_PAYLOAD_MAX_BYTES,
) -> None:
    """Check `raw_event["text"]` UTF-8 byte length; raise PayloadTooLarge on excess.

    Callers MUST run this BEFORE passing the dict to pydantic EntryEvent
    validation so that oversize payloads never enter the hashing / audit
    paths with their full content.
    """
    text = raw_event.get("text")
    user_id = raw_event.get("userId")
    uid_raw = user_id if isinstance(user_id, str) else None

    if not isinstance(text, str):
        raise PayloadTooLarge(
            actual_bytes=-1, limit_bytes=limit_bytes, user_id_raw=uid_raw
        )
    actual = len(text.encode("utf-8"))
    if actual > limit_bytes:
        raise PayloadTooLarge(
            actual_bytes=actual, limit_bytes=limit_bytes, user_id_raw=uid_raw
        )


_ACTOR_USER_RE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


def _is_actor_safe(user_id: str) -> bool:
    if not user_id or len(user_id) > 128:
        return False
    return all(c in _ACTOR_USER_RE_CHARS for c in user_id)


def build_too_large_audit(
    exc: PayloadTooLarge,
    *,
    user_id: str | None = None,
) -> AuditEvent:
    """Produce an AuditEvent capturing the rejection.

    If `user_id` does not satisfy the actor pattern the audit still lands
    (`actor="system"`) so we have a forensic record even for malicious input;
    the raw value goes into `extra.raw_user_id` for later investigation.
    """
    uid = user_id or exc.user_id_raw or ""
    if uid and _is_actor_safe(uid):
        actor = f"user:{uid}"
        extra: dict[str, Any] = {
            "actual_bytes": exc.actual_bytes,
            "limit_bytes": exc.limit_bytes,
        }
    else:
        actor = "system"
        extra = {
            "actual_bytes": exc.actual_bytes,
            "limit_bytes": exc.limit_bytes,
            "raw_user_id": uid or "<absent>",
        }
    return AuditEvent.model_validate(
        {
            "auditId": _new_audit_id(),
            "timestamp": datetime.now(tz=UTC),
            "actor": actor,
            "eventType": "event_rejected_too_large",
            "outcome": "rejected",
            "extra": extra,
        }
    )


def _new_audit_id() -> str:
    """Simple 32-char uuid-based id for pre-Trace audit events. ULID comes later."""
    return uuid4().hex
