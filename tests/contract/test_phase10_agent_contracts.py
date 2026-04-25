"""Phase 10 contract tests for main / sub agent collaboration.

These tests freeze the first batch of Phase 10 message contracts before
implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orchestrator_kernel.contracts.phase10 import (
    AgentCancelRequest,
    AgentCancelResponse,
    AgentCapability,
    AgentHealthReport,
    AgentRequest,
    AgentStatusRequest,
    AgentStatusResponse,
    AgentTaskRequest,
    AgentTaskResponse,
    RouteDecision,
)


class TestAgentRequest:
    def test_accepts_valid_request(self) -> None:
        model = AgentRequest.model_validate(
            {
                "text": "echo hello",
                "userId": "alice",
                "eventId": "01KQ1PHASE1000000000000000",
                "sourceChannel": "cli",
            }
        )
        assert model.text == "echo hello"
        assert model.sourceChannel == "cli"

    @pytest.mark.parametrize("field", ["text", "userId", "sourceChannel"])
    def test_rejects_missing_required(self, field: str) -> None:
        payload = {
            "text": "echo hello",
            "userId": "alice",
            "eventId": "01KQ1PHASE1000000000000000",
            "sourceChannel": "cli",
        }
        del payload[field]
        with pytest.raises(ValidationError):
            AgentRequest.model_validate(payload)

    def test_rejects_unknown_source_channel(self) -> None:
        payload = {
            "text": "echo hello",
            "userId": "alice",
            "eventId": "01KQ1PHASE1000000000000000",
            "sourceChannel": "telegram",
        }
        with pytest.raises(ValidationError):
            AgentRequest.model_validate(payload)


class TestRouteDecision:
    def test_accepts_valid_decision(self) -> None:
        model = RouteDecision.model_validate(
            {
                "traceId": "01KQ1TRACE0000000000000000",
                "eventId": "01KQ1PHASE1000000000000000",
                "selectedAgentId": "worker-echo-01",
                "selectedCapability": "echo.say",
                "decisionReason": "capability match",
                "decisionType": "capability_match",
                "fallbackUsed": False,
                "timestamp": "2026-04-25T12:00:00Z",
            }
        )
        assert model.selectedCapability == "echo.say"


class TestAgentCapability:
    def test_accepts_valid_capability(self) -> None:
        model = AgentCapability.model_validate(
            {
                "agentId": "worker-echo-01",
                "capability": "echo.say",
                "version": "1.0.0",
                "riskLevel": "NORMAL",
                "healthy": True,
                "resourceLimits": {
                    "memoryMb": 128,
                    "cpuPct": 10,
                    "wallClockMs": 60000,
                },
                "description": "Echo worker",
                "lastHeartbeatAt": "2026-04-25T12:00:00Z",
            }
        )
        assert model.healthy is True

    def test_rejects_unknown_risk_level(self) -> None:
        payload = {
            "agentId": "worker-echo-01",
            "capability": "echo.say",
            "version": "1.0.0",
            "riskLevel": "MEDIUM",
            "healthy": True,
            "resourceLimits": {
                "memoryMb": 128,
                "cpuPct": 10,
                "wallClockMs": 60000,
            },
        }
        with pytest.raises(ValidationError):
            AgentCapability.model_validate(payload)


class TestAgentTaskRequest:
    def test_accepts_valid_task_request(self) -> None:
        model = AgentTaskRequest.model_validate(
            {
                "traceId": "01KQ1TRACE0000000000000000",
                "eventId": "01KQ1PHASE1000000000000000",
                "taskId": "01KQ1TASK00000000000000000",
                "parentTaskId": None,
                "capability": "echo.say",
                "payload": {"text": "hello"},
                "riskLevel": "NORMAL",
                "sourceChannel": "cli",
                "userId": "alice",
                "deadlineAt": "2026-04-25T12:00:05Z",
                "attempt": 1,
                "metadata": {},
            }
        )
        assert model.attempt == 1


class TestAgentTaskResponse:
    def test_accepts_success_response(self) -> None:
        model = AgentTaskResponse.model_validate(
            {
                "traceId": "01KQ1TRACE0000000000000000",
                "eventId": "01KQ1PHASE1000000000000000",
                "taskId": "01KQ1TASK00000000000000000",
                "agentId": "worker-echo-01",
                "status": "succeeded",
                "result": {"text": "hello"},
                "errorCode": None,
                "errorMessage": None,
                "failureReason": None,
                "failureDim": None,
                "startedAt": "2026-04-25T12:00:00Z",
                "finishedAt": "2026-04-25T12:00:00Z",
                "outputHash": None,
                "metadata": {},
            }
        )
        assert model.status == "succeeded"

    def test_rejects_status_without_result_when_succeeded(self) -> None:
        payload = {
            "traceId": "01KQ1TRACE0000000000000000",
            "eventId": "01KQ1PHASE1000000000000000",
            "taskId": "01KQ1TASK00000000000000000",
            "agentId": "worker-echo-01",
            "status": "succeeded",
            "result": None,
            "errorCode": None,
            "errorMessage": None,
            "failureReason": None,
            "failureDim": None,
            "startedAt": "2026-04-25T12:00:00Z",
            "finishedAt": "2026-04-25T12:00:00Z",
            "outputHash": None,
            "metadata": {},
        }
        with pytest.raises(ValidationError):
            AgentTaskResponse.model_validate(payload)


class TestCancelAndStatus:
    def test_accepts_cancel_request(self) -> None:
        model = AgentCancelRequest.model_validate(
            {
                "traceId": "01KQ1TRACE0000000000000000",
                "taskId": None,
                "userId": "alice",
                "reason": "user_requested",
                "timestamp": "2026-04-25T12:00:02Z",
                "sourceChannel": "cli",
            }
        )
        assert model.userId == "alice"

    def test_accepts_status_response(self) -> None:
        model = AgentStatusResponse.model_validate(
            {
                "traceId": "01KQ1TRACE0000000000000000",
                "agentId": None,
                "status": "running",
                "message": "trace in progress",
                "lastKnownState": "running",
                "timestamp": "2026-04-25T12:00:04Z",
            }
        )
        assert model.status == "running"


class TestHealth:
    def test_accepts_health_report(self) -> None:
        model = AgentHealthReport.model_validate(
            {
                "agentId": "worker-echo-01",
                "healthy": True,
                "capability": "echo.say",
                "heartbeatAt": "2026-04-25T12:00:05Z",
                "resourceUsage": {
                    "memoryMb": 23.4,
                    "cpuPct": 1.2,
                    "runtimeMs": 15000,
                },
                "details": None,
            }
        )
        assert model.healthy is True
