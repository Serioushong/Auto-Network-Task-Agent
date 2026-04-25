"""Feishu SDK-based intake for the orchestrator kernel.

This module listens for Feishu event subscriptions via the official
Python SDK, maps the received message event into the internal kernel
payload shape, and forwards it into the shared Phase 10 execution path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .feishu_common import extract_payload_fields, handle_payload as shared_handle_payload

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def _dispatch_internal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("feishu_sdk_internal_payload=%s", payload)
    return await shared_handle_payload(payload)


def _message_event_to_payload(data: Any) -> dict[str, Any]:
    header = getattr(data, "header", None)
    event = getattr(data, "event", None)
    message = getattr(event, "message", None) if event is not None else None
    sender = getattr(event, "sender", None) if event is not None else None
    sender_id = getattr(sender, "sender_id", None) if sender is not None else None

    content = getattr(message, "content", None)
    text = None
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                text = parsed.get("text") if isinstance(parsed.get("text"), str) else content
            else:
                text = content
        except json.JSONDecodeError:
            text = content

    user_id = None
    if sender_id is not None:
        user_id = getattr(sender_id, "user_id", None) or getattr(sender_id, "open_id", None) or getattr(sender_id, "union_id", None)

    event_id = None
    if header is not None:
        event_id = getattr(header, "event_id", None)
    if event_id is None and message is not None:
        event_id = getattr(message, "message_id", None)

    return {
        "text": text,
        "userId": user_id,
        "eventId": event_id,
    }


async def handle_sdk_event(data: Any) -> dict[str, Any]:
    payload = _message_event_to_payload(data)
    logger.info("feishu_sdk_mapped_payload=%s", payload)
    print(f"[feishu_sdk_event] mapped_payload={payload}", flush=True)
    return await _dispatch_internal_payload(payload)


def build_event_handler() -> Any:
    import lark_oapi as lark

    def on_message(data: Any) -> None:
        logger.info("feishu_sdk_event_received=%s", data)
        asyncio.run(handle_sdk_event(data))

    dispatcher = lark.EventDispatcherHandler.builder(
        os.environ.get("FEISHU_APP_ID", ""),
        os.environ.get("FEISHU_APP_SECRET", ""),
    ).register_p2_im_message_receive_v1(on_message).build()
    return dispatcher


def main() -> None:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

    import lark_oapi as lark

    logger.info("starting feishu sdk listener app_id=%s", app_id)
    event_handler = build_event_handler()
    ws_client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
    )
    ws_client.start()


if __name__ == "__main__":
    main()
