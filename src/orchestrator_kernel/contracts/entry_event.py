"""T016 — EntryEvent pydantic mirror of `contracts/entry-event.schema.json`.

External channel delivery to the kernel. Part of the idempotency key
(userId, eventId). Enforces FR-031: `text` UTF-8 byte length <= 16 KB
**by byte count**, not character count — so a 5_462-char Chinese string
(~16_386 bytes) is rejected even though `len(text) < 16_384`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceChannel = Literal["cli", "http", "feishu_stub"]

TEXT_BYTE_CAP: int = 16_384
"""FR-031 hard cap: UTF-8 byte length. Oversize -> `event_rejected_too_large`."""


class EntryEvent(BaseModel):
    """One delivery from a sourceChannel. (userId, eventId) is the idempotency key."""

    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=16, max_length=64)
    userId: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=TEXT_BYTE_CAP)
    sourceChannel: SourceChannel
    receivedAt: datetime
    meta: dict[str, Any] | None = None

    @field_validator("text")
    @classmethod
    def _enforce_utf8_byte_cap(cls, value: str) -> str:
        if len(value.encode("utf-8")) > TEXT_BYTE_CAP:
            raise ValueError(
                f"text exceeds {TEXT_BYTE_CAP}-byte UTF-8 cap (FR-031)"
            )
        return value
