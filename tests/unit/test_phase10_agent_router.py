from __future__ import annotations

import pytest

from orchestrator_kernel.contracts.phase10 import AgentCapability, AgentRequest
from orchestrator_kernel.kernel.dispatcher import Dispatcher, WorkerHandle
from orchestrator_kernel.phase10_agent import AgentRegistry, MainAgentRouter, MainAgentRuntime


def _capability(agent_id: str = "worker-echo-01", healthy: bool = True) -> AgentCapability:
    return AgentCapability.model_validate(
        {
            "agentId": agent_id,
            "capability": "echo.say",
            "version": "1.0.0",
            "riskLevel": "NORMAL",
            "healthy": healthy,
            "resourceLimits": {
                "memoryMb": 128,
                "cpuPct": 10,
                "wallClockMs": 60000,
            },
            "description": "Echo worker",
            "lastHeartbeatAt": "2026-04-25T12:00:00Z",
        }
    )


def _request(text: str = "echo hello") -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "text": text,
            "userId": "alice",
            "eventId": "01KQ1PHASE1000000000000000",
            "sourceChannel": "cli",
        }
    )


def test_registry_register_and_lookup() -> None:
    registry = AgentRegistry()
    cap = _capability()
    registry.register(cap)
    assert registry.get(cap.agentId) == cap
    assert registry.find_by_capability("echo.say") == cap


def test_router_decides_capability_match() -> None:
    dispatcher = Dispatcher()
    registry = AgentRegistry()
    cap = _capability()
    registry.register(cap)
    dispatcher.register(
        WorkerHandle(
            worker_id=cap.agentId,
            capabilities=tuple(),
            healthy=True,
            metadata={"capability": cap.capability},
        )
    )
    router = MainAgentRouter(dispatcher, registry)

    result = router.decide(_request())
    assert result.decision.decisionType == "capability_match"
    assert result.worker is not None
    assert result.worker.worker_id == cap.agentId


def test_router_reports_no_match_when_registry_empty() -> None:
    router = MainAgentRouter(Dispatcher(), AgentRegistry())
    result = router.decide(_request())
    assert result.decision.decisionType == "no_match"
    assert result.worker is None


def test_router_reports_unhealthy_when_worker_missing() -> None:
    dispatcher = Dispatcher()
    registry = AgentRegistry()
    cap = _capability()
    registry.register(cap)
    router = MainAgentRouter(dispatcher, registry)

    result = router.decide(_request())
    assert result.decision.decisionType == "unhealthy"
    assert result.worker is None


def test_dispatch_materializes_task_payload() -> None:
    dispatcher = Dispatcher()
    registry = AgentRegistry()
    cap = _capability()
    registry.register(cap)
    dispatcher.register(
        WorkerHandle(
            worker_id=cap.agentId,
            capabilities=tuple(),
            healthy=True,
            metadata={"capability": cap.capability},
        )
    )
    router = MainAgentRouter(dispatcher, registry)

    result = router.dispatch(_request())
    assert result.worker.worker_id == cap.agentId
    assert result.task_payload["capability"] == "echo.say"
    assert result.task_payload["text"] == "echo hello"


def test_runtime_submit_delegates_dispatch() -> None:
    dispatcher = Dispatcher()
    registry = AgentRegistry()
    cap = _capability()
    registry.register(cap)
    dispatcher.register(
        WorkerHandle(
            worker_id=cap.agentId,
            capabilities=tuple(),
            healthy=True,
            metadata={"capability": cap.capability},
        )
    )
    runtime = MainAgentRuntime(router=MainAgentRouter(dispatcher, registry))
    result = runtime.submit(_request())
    assert result.worker.worker_id == cap.agentId
