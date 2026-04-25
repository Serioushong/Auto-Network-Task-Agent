"""Phase 10 entrypoint adapters.

These helpers expose the Phase 10 main-agent runtime through thin
entrypoint-facing functions so CLI / HTTP / future Feishu adapters can
share the same request -> dispatch path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts.phase10 import AgentRequest
from .phase10_agent import MainAgentRuntime


@dataclass(frozen=True)
class Phase10EntrypointResult:
    trace_id: str
    event_id: str
    selected_agent_id: str
    selected_capability: str
    task_payload: dict[str, Any]


class Phase10EntrypointAdapter:
    def __init__(self, runtime: MainAgentRuntime) -> None:
        self._runtime = runtime

    def submit(
        self,
        *,
        text: str,
        user_id: str,
        event_id: str | None,
        source_channel: str,
        trace_id: str | None = None,
    ) -> Phase10EntrypointResult:
        request = AgentRequest.model_validate(
            {
                "text": text,
                "userId": user_id,
                "eventId": event_id,
                "sourceChannel": source_channel,
                "traceId": trace_id,
            }
        )
        result = self._runtime.submit(request)
        return Phase10EntrypointResult(
            trace_id=result.decision.traceId,
            event_id=result.decision.eventId,
            selected_agent_id=result.decision.selectedAgentId,
            selected_capability=result.decision.selectedCapability,
            task_payload=result.task_payload,
        )


__all__ = ["Phase10EntrypointAdapter", "Phase10EntrypointResult"]
