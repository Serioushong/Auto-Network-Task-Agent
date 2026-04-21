"""T014 — contract test for AuditEvent.

References `contracts/audit-event.schema.json`. RED until T023.

Covers:
- All 36 `eventType` enum values (from schema) accepted.
- `actor` pattern: kernel | system | worker:<id> | user:<id>.
- `input_hash` / `output_hash` pattern (`^[0-9a-f]{32}$`).
- Nullable `traceId` (pre-Trace events like rate_limited_rejection).
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.audit import AuditEvent
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects, load_schema

SCHEMA = "audit-event.schema.json"


def _valid() -> dict:
    return {
        "auditId": "01J9AUDIT000000000",
        "timestamp": "2026-04-21T00:00:00Z",
        "traceId": "01J9TRACE000000000",
        "taskId": "01J9LEAF0000000000",
        "actor": "kernel",
        "eventType": "task_created",
    }


# Mirror of schema $.properties.eventType.enum — T014 demands all 36 literals.
ALL_EVENT_TYPES: list[str] = [
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


class TestSchemaEventTypeInventory:
    def test_schema_enum_matches_test_inventory(self) -> None:
        schema_enum = load_schema(SCHEMA)["properties"]["eventType"]["enum"]
        assert set(schema_enum) == set(ALL_EVENT_TYPES)
        # 38 actually (count of enum); spec.md says 36 — we trust schema as ground truth
        # but enforce parity between test and schema.


class TestEventTypeEnum:
    @pytest.mark.parametrize("et", ALL_EVENT_TYPES)
    def test_pydantic_accepts(self, et: str) -> None:
        instance = _valid() | {"eventType": et}
        AuditEvent.model_validate(instance)

    @pytest.mark.parametrize(
        "bad", ["event_received_", "TASK_CREATED", "", "unknown_event", "task.created"]
    )
    def test_invalid_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(_valid() | {"eventType": bad})


class TestActorPattern:
    @pytest.mark.parametrize(
        "actor",
        [
            "kernel",
            "system",
            "worker:echo-worker-01",
            "worker:a",
            "user:alice",
            "user:alice@local",
            "worker:a" + "b" * 127,  # 128 chars total after "worker:"
        ],
    )
    def test_valid(self, actor: str) -> None:
        AuditEvent.model_validate(_valid() | {"actor": actor})

    @pytest.mark.parametrize(
        "actor",
        [
            "",
            "Kernel",
            "worker",
            "worker:",
            "admin",
            "user:",
            "worker:invalid/char",
            "worker:" + "a" * 129,
        ],
    )
    def test_invalid(self, actor: str) -> None:
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(_valid() | {"actor": actor})


_OK_HASH = "0123456789abcdef0123456789abcdef"
_BAD_HASHES = [
    "0123",
    _OK_HASH.upper(),
    _OK_HASH + "0",
    "G" * 32,
]


class TestHashFields:
    def test_input_hash_accepts(self) -> None:
        AuditEvent.model_validate(_valid() | {"input_hash": _OK_HASH})

    def test_output_hash_accepts(self) -> None:
        AuditEvent.model_validate(_valid() | {"output_hash": _OK_HASH})

    @pytest.mark.parametrize("bad", _BAD_HASHES)
    def test_invalid_hash_rejected_on_input(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(_valid() | {"input_hash": bad})

    @pytest.mark.parametrize("bad", _BAD_HASHES)
    def test_invalid_hash_rejected_on_output(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(_valid() | {"output_hash": bad})


class TestNullableTraceId:
    def test_trace_id_can_be_null_pre_trace_events(self) -> None:
        instance = _valid() | {
            "eventType": "event_rejected_rate_limited",
            "traceId": None,
            "taskId": None,
        }
        ev = AuditEvent.model_validate(instance)
        assert ev.traceId is None
        assert_json_schema_accepts(SCHEMA, instance)

    def test_trace_id_absent_ok(self) -> None:
        instance = _valid() | {
            "eventType": "event_rejected_too_large",
        }
        instance.pop("traceId")
        instance.pop("taskId")
        AuditEvent.model_validate(instance)


class TestMissingRequired:
    @pytest.mark.parametrize("field", ["auditId", "timestamp", "actor", "eventType"])
    def test_missing_rejected(self, field: str) -> None:
        instance = _valid()
        del instance[field]
        with pytest.raises(ValidationError):
            AuditEvent.model_validate(instance)
        assert_json_schema_rejects(SCHEMA, instance)
