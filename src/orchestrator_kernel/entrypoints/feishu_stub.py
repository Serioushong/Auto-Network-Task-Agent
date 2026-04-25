"""T098 — Feishu stub entrypoint.

This module provides the smallest useful Feishu-shaped adapter for the
local MVP:

- accept a message payload
- map it to the existing kernel submit contract
- run the same execution pipeline used by CLI / HTTP
- print the result so a caller can forward it back to chat

It does not call the real Feishu API yet. That remains a later
integration step. The point here is to prove that Feishu can be a thin
adapter over the already-working kernel core.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ..contracts.phase10 import AgentCapability
from ..kernel.dispatcher import Dispatcher, WorkerHandle
from ..phase10_agent import AgentRegistry, MainAgentRouter, MainAgentRuntime
from ..phase10_entrypoints import Phase10EntrypointAdapter


class FeishuMessage(BaseModel):
    text: str = Field(min_length=1)
    userId: str = Field(min_length=1, max_length=128)
    eventId: str | None = Field(default=None, min_length=16, max_length=64)


def _feishu_adapter(audit_dir: Path) -> Phase10EntrypointAdapter:
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


async def handle_message(
    *,
    text: str,
    user_id: str,
    event_id: str | None = None,
    audit_dir: Path = Path("var/audit"),
) -> dict[str, Any]:
    """Submit a Feishu-shaped message through the shared adapter path."""
    adapter = _feishu_adapter(audit_dir)
    result = adapter.submit(
        text=text,
        user_id=user_id,
        event_id=event_id,
        source_channel="feishu_stub",
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


async def handle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    message = FeishuMessage.model_validate(payload)
    return await handle_message(
        text=message.text,
        user_id=message.userId,
        event_id=message.eventId,
    )


def create_app(audit_dir: Path = Path("var/audit")) -> FastAPI:
    app = FastAPI(title="Orchestrator Kernel Feishu Stub", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ready": True, "sourceChannel": "feishu_stub"}

    @app.post("/feishu/submit")
    async def submit(payload: dict[str, Any] = Body(...)) -> Any:
        try:
            return await handle_payload(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail={"errors": exc.errors()}) from exc

    return app


def main() -> None:
    """Read a JSON payload from stdin and execute it locally."""
    raw = json.load(__import__("sys").stdin)
    try:
        result = asyncio.run(handle_payload(raw))
    except ValidationError as exc:
        print(json.dumps({"error": exc.errors()}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()


__all__ = ["FeishuMessage", "create_app", "handle_message", "handle_payload", "main"]
