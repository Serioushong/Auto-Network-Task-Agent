"""Shared Feishu payload mapping and kernel dispatch helpers.

Kept free of transport-specific concerns so both webhook and SDK entrypoints
can reuse the same internal payload transformation and execution path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..contracts.phase10 import AgentCapability
from ..kernel.dispatcher import Dispatcher, WorkerHandle
from ..phase10_agent import AgentRegistry, MainAgentRouter, MainAgentRuntime
from ..phase10_entrypoints import Phase10EntrypointAdapter


class FeishuMessage(BaseModel):
    text: str = Field(min_length=1)
    userId: str = Field(min_length=1, max_length=128)
    eventId: str | None = Field(default=None, min_length=16, max_length=64)


def build_runtime() -> Phase10EntrypointAdapter:
    registry = AgentRegistry()
    capability = AgentCapability.model_validate(
        {
            "agentId": "feishu-phase10-echo",
            "capability": "echo.say",
            "version": "1.0.0",
            "riskLevel": "NORMAL",
            "healthy": True,
            "resourceLimits": {"memoryMb": 128, "cpuPct": 10, "wallClockMs": 60000},
        }
    )
    registry.register(capability)
    dispatcher = Dispatcher()
    dispatcher.register(
        WorkerHandle(
            worker_id=capability.agentId,
            capabilities=tuple(),
            healthy=True,
            metadata={"capability": capability.capability},
        )
    )
    runtime = MainAgentRuntime(router=MainAgentRouter(dispatcher, registry))
    return Phase10EntrypointAdapter(runtime)


def extract_payload_fields(payload: dict[str, Any]) -> dict[str, str | None]:
    if "text" in payload and isinstance(payload.get("text"), str):
        return {
            "text": payload["text"],
            "userId": payload.get("userId") if isinstance(payload.get("userId"), str) else None,
            "eventId": payload.get("eventId") if isinstance(payload.get("eventId"), str) else None,
        }

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}

    text = message.get("content") or message.get("text") or payload.get("text")
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                text = parsed["text"]
        except json.JSONDecodeError:
            pass

    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else None
    if isinstance(sender_id, dict):
        user_id = sender_id.get("user_id") or sender_id.get("open_id") or sender_id.get("union_id")
    else:
        user_id = (
            sender.get("sender_id")
            or sender.get("open_id")
            or sender.get("union_id")
            or payload.get("userId")
        )

    event_id = header.get("event_id") or event.get("message_id") or event.get("event_id") or payload.get("eventId")
    return {
        "text": text if isinstance(text, str) else None,
        "userId": user_id if isinstance(user_id, str) else None,
        "eventId": event_id if isinstance(event_id, str) else None,
    }


async def handle_payload(payload: dict[str, Any], audit_dir: Path = Path("var/audit")) -> dict[str, Any]:
    fields = extract_payload_fields(payload)
    message = FeishuMessage.model_validate(fields)
    adapter = build_runtime()
    result = adapter.submit(
        text=message.text,
        user_id=message.userId,
        event_id=message.eventId,
        source_channel="feishu",
    )
    return {
        "traceId": result.trace_id,
        "eventId": result.event_id,
        "traceOutcome": "feishu-dispatched",
        "leafOutcomes": [],
        "audit_event_types": [],
        "duration_s": 0.0,
        "message": result.selected_capability,
    }
