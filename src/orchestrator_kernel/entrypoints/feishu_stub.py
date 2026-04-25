"""T098 — Feishu stub / webhook entrypoint.

This module provides the smallest useful Feishu-shaped adapter for the
local MVP:

- accept a message payload
- map it to the existing kernel submit contract
- run the same execution pipeline used by CLI / HTTP
- print the result so a caller can forward it back to chat

It now also supports the minimal real webhook verification flow:
- challenge echo on initial URL verification
- optional verification token guard
- optional HMAC-SHA256 signature guard
- Feishu-style webhook payload extraction
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

from fastapi.responses import JSONResponse
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


def _extract_payload_fields(payload: dict[str, Any]) -> dict[str, str | None]:
    if "text" in payload and isinstance(payload.get("text"), str):
        return {
            "text": payload["text"],
            "userId": payload.get("userId") if isinstance(payload.get("userId"), str) else None,
            "eventId": payload.get("eventId") if isinstance(payload.get("eventId"), str) else None,
        }
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    text = message.get("text") or message.get("content") or payload.get("text")
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                text = parsed["text"]
        except json.JSONDecodeError:
            pass
    user_id = (
        sender.get("sender_id")
        or sender.get("open_id")
        or sender.get("union_id")
        or payload.get("userId")
    )
    event_id = event.get("message_id") or event.get("event_id") or payload.get("eventId")
    return {
        "text": text if isinstance(text, str) else None,
        "userId": user_id if isinstance(user_id, str) else None,
        "eventId": event_id if isinstance(event_id, str) else None,
    }


def _constant_time_eq(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return hmac.compare_digest(left, right)


def _verify_webhook(
    *,
    body: bytes,
    token_header: str | None,
    signature_header: str | None,
    timestamp_header: str | None,
    nonce_header: str | None,
) -> None:
    expected_token = os.environ.get("FEISHU_VERIFICATION_TOKEN") or os.environ.get(
        "FEISHU_WEBHOOK_VERIFICATION_TOKEN"
    ) or None
    expected_secret = os.environ.get("FEISHU_ENCRYPT_KEY") or os.environ.get(
        "FEISHU_WEBHOOK_APP_SECRET"
    ) or None
    stub_token = os.environ.get("FEISHU_STUB_TOKEN") or None

    if expected_token:
        if not _constant_time_eq(token_header, expected_token):
            raise HTTPException(status_code=401, detail="invalid webhook verification token")
    elif stub_token:
        if not _constant_time_eq(token_header, stub_token):
            raise HTTPException(status_code=401, detail="invalid stub token")

    if expected_secret:
        if not (signature_header and timestamp_header and nonce_header):
            raise HTTPException(status_code=401, detail="missing signature headers")
        canonical = f"{timestamp_header}\n{nonce_header}\n".encode("utf-8") + body
        digest = hmac.new(expected_secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        expected_sig = f"v1={digest}"
        if not _constant_time_eq(signature_header, expected_sig):
            raise HTTPException(status_code=401, detail="invalid webhook signature")


async def handle_message(
    *,
    text: str,
    user_id: str,
    event_id: str | None = None,
    audit_dir: Path = Path("var/audit"),
) -> dict[str, Any]:
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
    fields = _extract_payload_fields(payload)
    message = FeishuMessage.model_validate(fields)
    return await handle_message(text=message.text, user_id=message.userId, event_id=message.eventId)


def create_app(audit_dir: Path = Path("var/audit"), *, expected_token: str | None = None) -> FastAPI:
    app = FastAPI(title="Orchestrator Kernel Feishu Stub", version="0.1.0")
    if expected_token is not None:
        os.environ["FEISHU_STUB_TOKEN"] = expected_token

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ready": True, "sourceChannel": "feishu_stub"}

    async def _process_request(request: Request) -> JSONResponse:
        raw_body = await request.body()
        headers_snapshot = {k: v for k, v in request.headers.items()}
        print(f"[feishu_webhook_request] path={request.url.path} body={raw_body.decode('utf-8', errors='replace')} headers={headers_snapshot}", flush=True)
        logger.info(
            "feishu_webhook_request received path=%s body=%s headers=%s",
            request.url.path,
            raw_body.decode("utf-8", errors="replace"),
            headers_snapshot,
        )
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            print(f"[feishu_webhook_request] invalid_json path={request.url.path} body={raw_body!r}", flush=True)
            logger.warning("feishu_webhook_request invalid_json path=%s", request.url.path)
            raise HTTPException(status_code=400, detail="invalid json body") from exc
        if isinstance(payload, dict):
            challenge = None
            if isinstance(payload.get("CHALLENGE"), str):
                challenge = payload["CHALLENGE"]
            elif isinstance(payload.get("challenge"), str):
                challenge = payload["challenge"]
            if challenge is not None:
                print(f"[feishu_webhook_request] challenge_echo path={request.url.path} challenge={challenge}", flush=True)
                logger.info("feishu_webhook_request challenge_echo path=%s", request.url.path)
                return JSONResponse(content={"CHALLENGE": challenge})
        _verify_webhook(
            body=raw_body,
            token_header=request.headers.get("x-feishu-token"),
            signature_header=request.headers.get("x-lark-signature"),
            timestamp_header=request.headers.get("x-lark-request-timestamp"),
            nonce_header=request.headers.get("x-lark-request-nonce"),
        )
        try:
            result = await handle_payload(payload)
            return JSONResponse(content=result)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail={"errors": exc.errors()}) from exc

    @app.post("/feishu/submit")
    async def submit(request: Request) -> JSONResponse:
        return await _process_request(request)

    @app.post("/feishu/webhook")
    async def webhook(request: Request) -> JSONResponse:
        return await _process_request(request)

    return app


def main() -> None:
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
