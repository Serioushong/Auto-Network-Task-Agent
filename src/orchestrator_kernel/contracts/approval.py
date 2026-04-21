"""T021 — Approval message pydantic mirror of `contracts/approval-message.schema.json`.

HIGH_RISK approval flow (FR-010 / FR-011 / FR-012). Discriminated union of
`approval_request` (Kernel -> User) and `approval_response` (User -> Kernel).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalRequest(_Msg):
    """Kernel -> User via sourceChannel. riskLevel is locked to HIGH_RISK."""

    kind: Literal["approval_request"]
    traceId: str = Field(min_length=16, max_length=64)
    taskId: str = Field(min_length=16, max_length=64)
    capability: str = Field(max_length=64)
    riskLevel: Literal["HIGH_RISK"]
    summary: str = Field(max_length=512)
    expiresAt: datetime


class ApprovalResponse(_Msg):
    """User -> Kernel via sourceChannel.

    userId MUST match Trace.userId at the kernel side; else the kernel audits
    `approval_impersonation_rejected` and ignores the message (FR-012).
    """

    kind: Literal["approval_response"]
    traceId: str = Field(min_length=16, max_length=64)
    decision: Literal["approve", "deny"]
    userId: str = Field(max_length=128)
    receivedAt: datetime


AnyApprovalMessage = Annotated[
    Union[ApprovalRequest, ApprovalResponse],  # noqa: UP007
    Field(discriminator="kind"),
]

ApprovalMessage: TypeAdapter[AnyApprovalMessage] = TypeAdapter(AnyApprovalMessage)
"""Use `.validate_python({...})` to parse an approval message."""
