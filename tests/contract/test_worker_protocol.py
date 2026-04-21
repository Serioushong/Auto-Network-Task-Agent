"""T011 — contract test for Worker ↔ Kernel stdio protocol frames.

References `contracts/worker-protocol.schema.json`. RED until T020.

Covers all 7 frame kinds (register / dispatch / started / result / heartbeat /
abort / shutdown) with positive + negative cases, plus `kind` discriminator
errors.
"""

from __future__ import annotations

import pytest
from orchestrator_kernel.contracts.worker_protocol import WorkerFrame
from pydantic import ValidationError

from ._common import assert_json_schema_accepts, assert_json_schema_rejects

SCHEMA = "worker-protocol.schema.json"


def _register_frame() -> dict:
    return {
        "kind": "register",
        "registration": {
            "workerId": "echo-worker-01",
            "pid": 12345,
            "capabilities": [
                {
                    "name": "echo.say",
                    "riskLevel": "NORMAL",
                    "budget": {
                        "wall_clock_ms": 60_000,
                        "max_tool_calls": 10,
                        "max_tokens": 20_000,
                    },
                }
            ],
            "resourceLimits": {
                "memory_mb": 256,
                "cpu_pct": 100,
                "wall_clock_ms": 60_000,
            },
        },
    }


def _dispatch_frame() -> dict:
    return {
        "kind": "dispatch",
        "taskId": "01J9LEAF0000000000",
        "traceId": "01J9TRACE000000000",
        "capability": "echo.say",
        "payload": {"message": "hi"},
        "budget": {
            "wall_clock_ms": 60_000,
            "max_tool_calls": 10,
            "max_tokens": 20_000,
        },
        "deadline": "2026-04-21T00:01:00Z",
    }


def _started_frame() -> dict:
    return {
        "kind": "started",
        "taskId": "01J9LEAF0000000000",
        "startedAt": "2026-04-21T00:00:01Z",
    }


def _result_frame() -> dict:
    return {
        "kind": "result",
        "taskId": "01J9LEAF0000000000",
        "outcome": "succeeded",
        "resultHash": "0123456789abcdef0123456789abcdef",
        "output": {"echoed": "hi"},
        "finishedAt": "2026-04-21T00:00:02Z",
    }


def _heartbeat_frame() -> dict:
    return {
        "kind": "heartbeat",
        "workerId": "echo-worker-01",
        "timestamp": "2026-04-21T00:00:05Z",
        "activeTaskIds": [],
    }


def _abort_frame() -> dict:
    return {
        "kind": "abort",
        "taskId": "01J9LEAF0000000000",
        "reason": "user_cancel",
    }


def _shutdown_frame() -> dict:
    return {"kind": "shutdown"}


ALL_FRAMES = {
    "register": _register_frame,
    "dispatch": _dispatch_frame,
    "started": _started_frame,
    "result": _result_frame,
    "heartbeat": _heartbeat_frame,
    "abort": _abort_frame,
    "shutdown": _shutdown_frame,
}


class TestAllFramesAccepted:
    @pytest.mark.parametrize("kind", list(ALL_FRAMES))
    def test_pydantic(self, kind: str) -> None:
        WorkerFrame.validate_python(ALL_FRAMES[kind]())

    @pytest.mark.parametrize("kind", list(ALL_FRAMES))
    def test_schema(self, kind: str) -> None:
        assert_json_schema_accepts(SCHEMA, ALL_FRAMES[kind]())


class TestKindDiscriminator:
    @pytest.mark.parametrize(
        "bad_kind", ["registration", "dispatched", "REGISTER", "", "ping"]
    )
    def test_unknown_kind_rejected(self, bad_kind: str) -> None:
        frame = _heartbeat_frame() | {"kind": bad_kind}
        with pytest.raises(ValidationError):
            WorkerFrame.validate_python(frame)

    def test_kind_missing_rejected(self) -> None:
        frame = _heartbeat_frame()
        del frame["kind"]
        with pytest.raises(ValidationError):
            WorkerFrame.validate_python(frame)


class TestDispatchRequired:
    @pytest.mark.parametrize(
        "field",
        ["taskId", "traceId", "capability", "payload", "budget", "deadline"],
    )
    def test_missing_rejected(self, field: str) -> None:
        frame = _dispatch_frame()
        del frame[field]
        with pytest.raises(ValidationError):
            WorkerFrame.validate_python(frame)
        assert_json_schema_rejects(SCHEMA, frame)


class TestResultOutcomeEnum:
    @pytest.mark.parametrize("outcome", ["succeeded", "failed"])
    def test_valid(self, outcome: str) -> None:
        frame = _result_frame() | {"outcome": outcome}
        if outcome == "failed":
            frame.pop("resultHash", None)
            frame["failureReason"] = "worker_internal_error"
        WorkerFrame.validate_python(frame)

    @pytest.mark.parametrize(
        "bad", ["cancelled", "denied", "killed", "SUCCEEDED", "unknown"]
    )
    def test_rejects_outcomes_outside_worker_scope(self, bad: str) -> None:
        # Worker-reported outcomes only include succeeded/failed.
        with pytest.raises(ValidationError):
            WorkerFrame.validate_python(_result_frame() | {"outcome": bad})


class TestAbortReasonEnum:
    @pytest.mark.parametrize(
        "reason", ["user_cancel", "budget_exceeded", "kernel_shutdown"]
    )
    def test_valid(self, reason: str) -> None:
        WorkerFrame.validate_python(_abort_frame() | {"reason": reason})

    @pytest.mark.parametrize("bad", ["cancel", "sigterm", "", "timeout"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            WorkerFrame.validate_python(_abort_frame() | {"reason": bad})
