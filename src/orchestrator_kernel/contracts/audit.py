"""T023 — AuditEvent pydantic mirror of `contracts/audit-event.schema.json`.

Append-only structured audit record. Single source of truth (FR-028) covering
FR-006 / FR-019 / FR-020 / FR-021 / FR-023. The 38-value `eventType` enum here
MUST remain in lockstep with the schema; T014 test enforces parity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AuditEventType = Literal[
    "event_received",
    "event_rejected_malformed",
    "event_rejected_too_large",
    "event_rejected_rate_limited",
    "event_rejected_warming_up",
    "trace_created",
    "idempotent_replay",
    "task_created",
    "task_pending_approval",
    "approval_granted",
    "approval_denied",
    "approval_timeout",
    "approval_impersonation_rejected",
    "approval_stale",
    "task_dispatched",
    "task_started",
    "task_succeeded",
    "task_failed",
    "cancel_requested",
    "cancel_not_found",
    "cancel_late",
    "soft_abort_sent",
    "hard_abort_sent",
    "task_cancelled",
    "worker_registered",
    "worker_heartbeat",
    "worker_unhealthy",
    "worker_recovered",
    "worker_terminated",
    "budget_exceeded",
    "sandbox_limit_hit",
    "kernel_restart_detected",
    "in_flight_auto_failed",
    "result_summary_prepared",
    "result_summary_delivered",
    "result_summary_retrying",
    "notification_delivery_failed",
    "disk_write_failed",
]

AuditOutcome = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "denied",
    "denied_by_timeout",
    "rejected",
]


class AuditEvent(BaseModel):
    """One append-only audit line. extra={} is ALREADY redacted per FR-020."""

    model_config = ConfigDict(extra="forbid")

    auditId: str = Field(min_length=16, max_length=64)
    timestamp: datetime
    traceId: str | None = Field(default=None, min_length=16, max_length=64)
    taskId: str | None = Field(default=None, min_length=16, max_length=64)
    parentTaskId: str | None = Field(default=None, min_length=16, max_length=64)
    actor: str = Field(
        pattern=(
            r"^(kernel|system|worker:[A-Za-z0-9_\-\.]{1,128}"
            r"|user:[A-Za-z0-9_\-\.]{1,128})$"
        )
    )
    capability: str | None = Field(default=None, max_length=64)
    eventType: AuditEventType
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    output_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    outcome: AuditOutcome | None = None
    idempotent_replay: bool | None = None
    extra: dict[str, Any] | None = None
