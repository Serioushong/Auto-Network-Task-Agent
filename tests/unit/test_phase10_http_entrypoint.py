from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator_kernel.contracts.phase10 import AgentCapability
from orchestrator_kernel.entrypoints.http import create_app
from orchestrator_kernel.kernel.dispatcher import Dispatcher, WorkerHandle
from orchestrator_kernel.phase10_agent import AgentRegistry, MainAgentRouter, MainAgentRuntime
from orchestrator_kernel.phase10_entrypoints import Phase10EntrypointAdapter


class _Harness:
    _ready = True

    async def submit(self, *args, **kwargs):
        raise AssertionError("fallback kernel path should not be used in this test")


def _adapter() -> Phase10EntrypointAdapter:
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
    runtime = MainAgentRuntime(router=MainAgentRouter(dispatcher, registry))
    return Phase10EntrypointAdapter(runtime)


def test_http_submit_uses_phase10_adapter() -> None:
    app = create_app(_Harness(), phase10_adapter=_adapter())
    client = TestClient(app)
    response = client.post(
        "/submit",
        json={"text": "echo hello", "userId": "alice", "eventId": "01KQ1PHASE1000000000000000"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["traceOutcome"] == "phase10-dispatched"
    assert body["message"] == "echo.say"


def test_http_healthz_still_works() -> None:
    app = create_app(_Harness(), phase10_adapter=_adapter())
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["sourceChannel"] == "http"
