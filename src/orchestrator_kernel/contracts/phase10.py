"""Phase 10 agent collaboration contracts.

These models describe the first revision of the main-agent / sub-agent
message boundary for Phase 10.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .worker import ResourceLimits

_SOURCE_CHANNELS = Literal["cli", "http", "feishu_stub"]
_RISK_LEVELS = Literal["NORMAL", "HIGH_RISK"]
_TASK_STATUSES = Literal["succeeded", "failed", "cancelled", "timed_out"]
_CANCEL_STATUSES = Literal[
    "accepted",
    "not_found",
    "already_terminal",
    "already_cancelled",
]
_STATUS_VALUES = Literal["running", "succeeded", "failed", "cancelled", "timed_out"]
_DECISION_TYPES = Literal["capability_match", "no_match", "unhealthy"]


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    userId: str = Field(min_length=1)
    eventId: str | None = None
    sourceChannel: _SOURCE_CHANNELS
    traceId: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str
    eventId: str
    selectedAgentId: str
    selectedCapability: str
    decisionReason: str
    decisionType: _DECISION_TYPES
    fallbackUsed: bool
    timestamp: datetime


class AgentCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    capability: str
    version: str
    riskLevel: _RISK_LEVELS
    healthy: bool
    resourceLimits: ResourceLimits
    description: str | None = None
    lastHeartbeatAt: datetime | None = None


class AgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str
    eventId: str
    taskId: str
    parentTaskId: str | None = None
    capability: str
    payload: dict[str, Any]
    riskLevel: _RISK_LEVELS
    sourceChannel: _SOURCE_CHANNELS
    userId: str
    deadlineAt: datetime | None = None
    attempt: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str
    eventId: str
    taskId: str
    agentId: str
    status: _TASK_STATUSES
    result: dict[str, Any] | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    failureReason: str | None = None
    failureDim: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    outputHash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_status_payload(self) -> "AgentTaskResponse":
        if self.status == "succeeded" and self.result is None:
            raise ValueError("result is required when status=succeeded")
        if self.status != "succeeded" and self.result is not None:
            raise ValueError("result must be null unless status=succeeded")
        if self.status in {"failed", "cancelled", "timed_out"}:
            if not any([self.errorCode, self.failureReason, self.errorMessage]):
                raise ValueError(
                    "failure details required when status is not succeeded"
                )
        return self


class AgentCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str
    taskId: str | None = None
    userId: str
    reason: str | None = None
    timestamp: datetime
    sourceChannel: _SOURCE_CHANNELS


class AgentCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str
    taskId: str | None = None
    status: _CANCEL_STATUSES
    message: str | None = None
    timestamp: datetime


class AgentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str | None = None
    agentId: str | None = None
    userId: str | None = None
    sourceChannel: _SOURCE_CHANNELS | None = None


class AgentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traceId: str | None = None
    agentId: str | None = None
    status: _STATUS_VALUES
    message: str | None = None
    lastKnownState: str | None = None
    timestamp: datetime


class AgentHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agentId: str
    healthy: bool
    capability: str
    heartbeatAt: datetime
    resourceUsage: dict[str, Any] | None = None
    details: str | None = None


__all__ = [
    "AgentCancelRequest",
    "AgentCancelResponse",
    "AgentCapability",
    "AgentHealthReport",
    "AgentRequest",
    "AgentStatusRequest",
    "AgentStatusResponse",
    "AgentTaskRequest",
    "AgentTaskResponse",
    "RouteDecision",
]
