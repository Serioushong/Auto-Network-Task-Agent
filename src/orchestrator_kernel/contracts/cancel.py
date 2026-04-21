"""T022 — Cancel message pydantic mirror of `contracts/cancel-message.schema.json`.

User -> Kernel cancel request (FR-013 / FR-014 / FR-015). userId MUST match
Trace.userId, or the kernel treats this like approval impersonation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CancelMessage(BaseModel):
    """Signed by the original event originator. Replay-safe per (traceId, userId)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cancel_request"]
    traceId: str = Field(min_length=16, max_length=64)
    userId: str = Field(max_length=128)
    receivedAt: datetime
