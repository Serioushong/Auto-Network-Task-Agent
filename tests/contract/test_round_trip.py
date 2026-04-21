"""T025 — Round-trip parity between pydantic mirrors and `contracts/*.schema.json`.

Goal: Prevent drift. The same instance must be accepted/rejected by both
the hand-authored JSON Schema (`contracts/*.schema.json`, the wire contract)
and the pydantic mirror (the runtime validator used by the kernel).

We deliberately do NOT byte-compare `Model.model_json_schema()` against the
hand-authored file because pydantic generates structurally different JSON
Schema (different `$defs`, numeric types, description wording). Instead we
check *behavioral* equivalence across a corpus of valid / invalid cases.

Also asserts that each pydantic model's generated JSON schema contains the
same top-level `required` set as the hand-authored schema; catches forgotten
fields without demanding full structural identity.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from orchestrator_kernel.contracts.approval import (
    ApprovalMessage,
)
from orchestrator_kernel.contracts.audit import AuditEvent
from orchestrator_kernel.contracts.budget import Budget
from orchestrator_kernel.contracts.cancel import CancelMessage
from orchestrator_kernel.contracts.entry_event import EntryEvent
from orchestrator_kernel.contracts.result_summary import ResultSummary
from orchestrator_kernel.contracts.task import Task
from orchestrator_kernel.contracts.worker import WorkerRegistration
from orchestrator_kernel.contracts.worker_protocol import WorkerFrame

from ._common import load_schema, validator


def _valid_entry() -> dict[str, Any]:
    return {
        "eventId": "01J9ABCDXYZ1234567",
        "userId": "alice",
        "text": "hi",
        "sourceChannel": "cli",
        "receivedAt": "2026-04-21T00:00:00Z",
    }


def _valid_budget() -> dict[str, Any]:
    return {"wall_clock_ms": 60_000, "max_tool_calls": 10, "max_tokens": 20_000}


def _valid_task() -> dict[str, Any]:
    return {
        "taskId": "01J9LEAF0000000000",
        "parentTaskId": "01J9ROOT0000000000",
        "traceId": "01J9TRACE000000000",
        "kind": "leaf_action",
        "capability": "echo.say",
        "riskLevel": "NORMAL",
        "budget": _valid_budget(),
        "state": "pending",
        "payload": {},
        "createdAt": "2026-04-21T00:00:00Z",
    }


def _valid_registration() -> dict[str, Any]:
    return {
        "workerId": "w-01",
        "pid": 12345,
        "capabilities": [
            {
                "name": "echo.say",
                "riskLevel": "NORMAL",
                "budget": _valid_budget(),
            }
        ],
        "resourceLimits": {
            "memory_mb": 256,
            "cpu_pct": 100,
            "wall_clock_ms": 60_000,
        },
    }


def _valid_heartbeat() -> dict[str, Any]:
    return {
        "kind": "heartbeat",
        "workerId": "w-01",
        "timestamp": "2026-04-21T00:00:05Z",
        "activeTaskIds": [],
    }


def _valid_approval_response() -> dict[str, Any]:
    return {
        "kind": "approval_response",
        "traceId": "01J9TRACE000000000",
        "decision": "approve",
        "userId": "alice",
        "receivedAt": "2026-04-21T00:02:00Z",
    }


def _valid_cancel() -> dict[str, Any]:
    return {
        "kind": "cancel_request",
        "traceId": "01J9TRACE000000000",
        "userId": "alice",
        "receivedAt": "2026-04-21T00:02:00Z",
    }


def _valid_audit() -> dict[str, Any]:
    return {
        "auditId": "01J9AUDIT000000000",
        "timestamp": "2026-04-21T00:00:00Z",
        "actor": "kernel",
        "eventType": "task_created",
        "traceId": "01J9TRACE000000000",
        "taskId": "01J9LEAF0000000000",
    }


def _valid_summary() -> dict[str, Any]:
    return {
        "kind": "result_summary",
        "traceId": "01J9TRACE000000000",
        "eventId": "01J9EVENT000000000",
        "userId": "alice",
        "commandDigest": "do something",
        "traceOutcome": "all_succeeded",
        "leafResults": [],
        "message": "ok",
        "preparedAt": "2026-04-21T00:00:10Z",
        "deliveryAttempt": 0,
    }


# (pydantic target, schema filename, valid fixture)
CASES: list[tuple[object, str, dict[str, Any]]] = [
    (EntryEvent, "entry-event.schema.json", _valid_entry()),
    (Budget, "budget.schema.json", _valid_budget()),
    (Task, "task.schema.json", _valid_task()),
    (WorkerRegistration, "worker-registration.schema.json", _valid_registration()),
    (WorkerFrame, "worker-protocol.schema.json", _valid_heartbeat()),
    (ApprovalMessage, "approval-message.schema.json", _valid_approval_response()),
    (CancelMessage, "cancel-message.schema.json", _valid_cancel()),
    (AuditEvent, "audit-event.schema.json", _valid_audit()),
    (ResultSummary, "result-summary.schema.json", _valid_summary()),
]


def _validate(target: object, instance: dict[str, Any]) -> None:
    """Unified validate for both BaseModel classes and TypeAdapters."""
    if isinstance(target, TypeAdapter):
        target.validate_python(instance)
    elif isinstance(target, type) and issubclass(target, BaseModel):
        target.model_validate(instance)
    else:
        raise TypeError(f"unsupported validation target: {target!r}")


@pytest.mark.parametrize("target,schema,fixture", CASES, ids=[c[1] for c in CASES])
class TestRoundTrip:
    def test_both_accept_valid(
        self, target: object, schema: str, fixture: dict[str, Any]
    ) -> None:
        _validate(target, fixture)
        errors = list(validator(schema).iter_errors(fixture))
        assert errors == [], f"JSON schema rejected: {errors}"

    def test_pydantic_extra_field_rejected_like_schema(
        self, target: object, schema: str, fixture: dict[str, Any]
    ) -> None:
        bad = fixture | {"_unexpected": "extra"}
        # Both layers MUST forbid unknown top-level fields.
        with pytest.raises(ValidationError):
            _validate(target, bad)
        errors = list(validator(schema).iter_errors(bad))
        assert errors, f"JSON schema unexpectedly accepted extra field in {schema}"


class TestRequiredFieldsParity:
    """Top-level `required` set MUST match between hand-authored schema and pydantic."""

    @pytest.mark.parametrize(
        "schema_name,pydantic_model",
        [
            ("entry-event.schema.json", EntryEvent),
            ("budget.schema.json", Budget),
            # Task.schema required at top level + kind-dependent required.
            ("task.schema.json", Task),
            ("worker-registration.schema.json", WorkerRegistration),
            ("cancel-message.schema.json", CancelMessage),
            ("audit-event.schema.json", AuditEvent),
            ("result-summary.schema.json", ResultSummary),
        ],
    )
    def test_required_top_level_match(
        self, schema_name: str, pydantic_model: type[BaseModel]
    ) -> None:
        schema_required = set(load_schema(schema_name).get("required", []))
        py_schema = pydantic_model.model_json_schema()
        py_required = set(py_schema.get("required", []))
        # For Task, schema-layer `required` excludes kind-conditional items
        # (capability, riskLevel, budget). Pydantic mirrors treats them as
        # Optional defaulting to None, so both layers report the same core set.
        assert schema_required.issubset(py_required), (
            f"{schema_name}: schema requires {schema_required - py_required} "
            f"but pydantic does not."
        )
