"""Phase 10 main-agent routing helpers.

This module introduces the smallest Phase 10 control-plane surface on top
of the existing dispatcher: a registry of sub-agent capabilities plus a
routing helper that emits a structured route decision.

It does not replace the current kernel pipeline yet. The goal for Batch C
and Batch E/F is to make the new main-agent concepts explicit and testable
while reusing existing dispatcher bookkeeping and audit infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .audit.writer import AuditWriter
from .contracts.audit import AuditEvent
from .contracts.phase10 import AgentCapability, AgentRequest, RouteDecision
from .kernel.dispatcher import Dispatcher, NoCapableWorkerError, WorkerHandle


@dataclass
class AgentRegistry:
    """In-memory registry of Phase 10 sub-agent capabilities."""

    _capabilities: dict[str, AgentCapability] = field(default_factory=dict)

    def register(self, capability: AgentCapability) -> None:
        self._capabilities[capability.agentId] = capability

    def get(self, agent_id: str) -> AgentCapability | None:
        return self._capabilities.get(agent_id)

    def known(self) -> tuple[AgentCapability, ...]:
        return tuple(self._capabilities.values())

    def find_by_capability(self, capability: str) -> AgentCapability | None:
        for item in self._capabilities.values():
            if item.capability == capability and item.healthy:
                return item
        return None


@dataclass(frozen=True)
class RouteResult:
    decision: RouteDecision
    worker: WorkerHandle | None


@dataclass(frozen=True)
class DispatchResult:
    decision: RouteDecision
    worker: WorkerHandle
    task_payload: dict[str, Any]


class MainAgentRouter:
    """Capability-based router that ties request -> registry -> dispatcher."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        registry: AgentRegistry,
        *,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._registry = registry
        self._audit_writer = audit_writer

    def _audit(
        self,
        *,
        event_type: str,
        request: AgentRequest,
        selected_agent_id: str | None = None,
        selected_capability: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._audit_writer is None:
            return
        self._audit_writer.write(
            AuditEvent.model_validate(
                {
                    "auditId": f"phase10-{event_type}-{request.eventId or request.traceId or 'unknown'}",
                    "timestamp": datetime.now(tz=timezone.utc),
                    "actor": "kernel",
                    "traceId": request.traceId,
                    "taskId": request.eventId,
                    "capability": selected_capability,
                    "eventType": event_type,
                    "outcome": extra.get("outcome") if extra else None,
                    "extra": {
                        "selectedAgentId": selected_agent_id,
                        **(extra or {}),
                    },
                }
            )
        )

    def _plan_capability(self, text: str) -> str:
        stripped = text.strip().lower()
        if stripped.startswith("echo"):
            return "echo.say"
        return stripped

    def decide(self, request: AgentRequest) -> RouteResult:
        """Return a structured routing decision and the matching worker handle."""
        planned_capability = self._plan_capability(request.text)
        capability = self._registry.find_by_capability(planned_capability)
        if capability is None:
            decision = RouteDecision(
                traceId=request.traceId or "",
                eventId=request.eventId or "",
                selectedAgentId="",
                selectedCapability=planned_capability,
                decisionReason="no registered capability matched request text",
                decisionType="no_match",
                fallbackUsed=False,
                timestamp=datetime.now(tz=timezone.utc),
            )
            self._audit(
                event_type="task_failed",
                request=request,
                selected_capability=planned_capability,
                extra={"decisionType": "no_match", "outcome": "failed", "failureReason": "no_match"},
            )
            return RouteResult(decision=decision, worker=None)

        handle = self._dispatcher.find_for(capability.capability)
        if handle is None:
            for candidate in self._dispatcher.workers():
                if candidate.worker_id == capability.agentId:
                    handle = candidate
                    break
        if handle is None:
            decision = RouteDecision(
                traceId=request.traceId or "",
                eventId=request.eventId or "",
                selectedAgentId=capability.agentId,
                selectedCapability=capability.capability,
                decisionReason="matching capability is unhealthy or unavailable",
                decisionType="unhealthy",
                fallbackUsed=False,
                timestamp=datetime.now(tz=timezone.utc),
            )
            self._audit(
                event_type="task_failed",
                request=request,
                selected_agent_id=capability.agentId,
                selected_capability=capability.capability,
                extra={"decisionType": "unhealthy", "outcome": "failed", "failureReason": "unhealthy"},
            )
            return RouteResult(decision=decision, worker=None)

        decision = RouteDecision(
            traceId=request.traceId or "",
            eventId=request.eventId or "",
            selectedAgentId=capability.agentId,
            selectedCapability=capability.capability,
            decisionReason="capability match",
            decisionType="capability_match",
            fallbackUsed=False,
            timestamp=datetime.now(tz=timezone.utc),
        )
        self._audit(
            event_type="task_started",
            request=request,
            selected_agent_id=capability.agentId,
            selected_capability=capability.capability,
            extra={"decisionType": "capability_match", "outcome": "succeeded"},
        )
        return RouteResult(decision=decision, worker=handle)

    def route(self, request: AgentRequest) -> WorkerHandle:
        """Resolve a worker handle or raise a no-capable-worker error."""
        result = self.decide(request)
        if result.worker is None:
            raise NoCapableWorkerError(result.decision.selectedCapability)
        return result.worker

    def dispatch(self, request: AgentRequest) -> DispatchResult:
        """Resolve a worker and materialize the payload for sub-agent dispatch."""
        result = self.decide(request)
        if result.worker is None:
            raise NoCapableWorkerError(result.decision.selectedCapability)
        task_payload = {
            "traceId": request.traceId or "",
            "eventId": request.eventId or "",
            "capability": result.decision.selectedCapability,
            "text": request.text,
            "userId": request.userId,
            "sourceChannel": request.sourceChannel,
            "metadata": request.metadata,
        }
        self._audit(
            event_type="task_dispatched",
            request=request,
            selected_agent_id=result.decision.selectedAgentId,
            selected_capability=result.decision.selectedCapability,
            extra={"outcome": "succeeded", "workerId": result.worker.worker_id},
        )
        return DispatchResult(
            decision=result.decision,
            worker=result.worker,
            task_payload=task_payload,
        )


@dataclass(frozen=True)
class MainAgentRuntime:
    """Tiny convenience facade for entrypoints that want Phase 10 routing."""

    router: MainAgentRouter

    def submit(self, request: AgentRequest) -> DispatchResult:
        return self.router.dispatch(request)


__all__ = [
    "AgentRegistry",
    "DispatchResult",
    "MainAgentRouter",
    "MainAgentRuntime",
    "RouteResult",
]
