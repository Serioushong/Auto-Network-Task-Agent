from __future__ import annotations

from pathlib import Path

from orchestrator_kernel.audit.writer import AuditWriter
from orchestrator_kernel.contracts.phase10 import AgentCapability, AgentRequest
from orchestrator_kernel.kernel.dispatcher import Dispatcher, WorkerHandle
from orchestrator_kernel.phase10_agent import AgentRegistry, MainAgentRouter


def _router(audit_dir: Path) -> MainAgentRouter:
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
    return MainAgentRouter(dispatcher, registry, audit_writer=AuditWriter(audit_dir))


def test_phase10_audit_replay_smoke(tmp_audit_dir: Path) -> None:
    router = _router(tmp_audit_dir)
    request = AgentRequest.model_validate(
        {
            "text": "echo hello",
            "userId": "alice",
            "eventId": "01KQ1PHASE1000000000000000",
            "sourceChannel": "cli",
            "traceId": "01KQ1TRACE0000000000000000",
        }
    )
    result = router.dispatch(request)
    assert result.worker.worker_id == "worker-echo-01"
    files = sorted(tmp_audit_dir.glob("audit-*.jsonl"))
    assert files, "expected audit output"
    body = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "task_started" in body
    assert "task_dispatched" in body
