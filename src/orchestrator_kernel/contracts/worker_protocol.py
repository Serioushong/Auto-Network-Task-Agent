"""T020 — Worker <-> Kernel stdio protocol pydantic mirror.

Source of truth: `contracts/worker-protocol.schema.json`. One JSON object per
line, UTF-8. The 7 frame kinds form a `kind`-discriminated union exposed as a
`TypeAdapter[AnyWorkerFrame]` under the name `WorkerFrame` for consumers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .budget import Budget
from .worker import WorkerRegistration

WorkerFailureReason = Literal[
    "worker_internal_error",
    "budget_exceeded",
    "invalid_payload",
]
FailureDim = Literal["wall", "tool", "token"]
AbortReason = Literal["user_cancel", "budget_exceeded", "kernel_shutdown"]


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterFrame(_Frame):
    """Worker -> Kernel: first frame after spawn."""

    kind: Literal["register"]
    registration: WorkerRegistration


class DispatchFrame(_Frame):
    """Kernel -> Worker: begin work on one leaf_action."""

    kind: Literal["dispatch"]
    taskId: str = Field(min_length=16, max_length=64)
    traceId: str = Field(min_length=16, max_length=64)
    capability: str = Field(max_length=64)
    payload: dict[str, Any]
    budget: Budget
    deadline: datetime


class StartedFrame(_Frame):
    """Worker -> Kernel: acknowledges dispatch and begins work."""

    kind: Literal["started"]
    taskId: str
    startedAt: datetime


class ResultFrame(_Frame):
    """Worker -> Kernel: final per-task report. Only succeeded / failed."""

    kind: Literal["result"]
    taskId: str
    outcome: Literal["succeeded", "failed"]
    resultHash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    output: dict[str, Any] | None = None
    failureReason: WorkerFailureReason | None = None
    failureDim: FailureDim | None = None
    finishedAt: datetime


class HeartbeatFrame(_Frame):
    """Worker -> Kernel: liveness signal. Missing 3 consecutive -> unhealthy (FR-009)."""

    kind: Literal["heartbeat"]
    workerId: str
    timestamp: datetime
    activeTaskIds: list[str] = Field(default_factory=list)


class AbortFrame(_Frame):
    """Kernel -> Worker: cooperative abort notice. OS signal sent in parallel per R-08."""

    kind: Literal["abort"]
    taskId: str
    reason: AbortReason


class ShutdownFrame(_Frame):
    """Kernel -> Worker: graceful exit request."""

    kind: Literal["shutdown"]


AnyWorkerFrame = Annotated[
    Union[  # noqa: UP007 — Annotated[Union,...] is the discriminator idiom for pydantic v2
        RegisterFrame,
        DispatchFrame,
        StartedFrame,
        ResultFrame,
        HeartbeatFrame,
        AbortFrame,
        ShutdownFrame,
    ],
    Field(discriminator="kind"),
]

WorkerFrame: TypeAdapter[AnyWorkerFrame] = TypeAdapter(AnyWorkerFrame)
"""Parse one stdio line: `WorkerFrame.validate_python(json.loads(line))`."""
