from __future__ import annotations

from orchestrator_kernel.contracts.phase10 import AgentCapability
from orchestrator_kernel.kernel.dispatcher import Dispatcher, WorkerHandle
from orchestrator_kernel.phase10_agent import AgentRegistry, MainAgentRouter, MainAgentRuntime
from orchestrator_kernel.phase10_entrypoints import Phase10EntrypointAdapter


def _runtime() -> MainAgentRuntime:
    registry = AgentRegistry()
    cap = AgentCapability.model_validate(
        {
            "agentId": "worker-echo-01",
            "capability": "echo.say",
            "version": "1.0.0",
            "riskLevel": "NORMAL",
            "healthy": True,
            "resourceLimits": {"memoryMb": 128, "cpuPct": 10, "wallClockMs": 60000},
        }
    )
    registry.register(cap)
    dispatcher = Dispatcher()
    dispatcher.register(
        WorkerHandle(
            worker_id=cap.agentId,
            capabilities=tuple(),
            healthy=True,
            metadata={"capability": cap.capability},
        )
    )
    return MainAgentRuntime(router=MainAgentRouter(dispatcher, registry))


def test_phase10_entrypoint_to_runtime_to_dispatch_payload() -> None:
    adapter = Phase10EntrypointAdapter(_runtime())
    result = adapter.submit(
        text="echo hello",
        user_id="alice",
        event_id="01KQ1PHASE1000000000000000",
        source_channel="cli",
    )
    assert result.selected_agent_id == "worker-echo-01"
    assert result.selected_capability == "echo.say"
    assert result.task_payload["text"] == "echo hello"
    assert result.task_payload["sourceChannel"] == "cli"
