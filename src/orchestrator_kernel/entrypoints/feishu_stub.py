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
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class FeishuMessage(BaseModel):
    text: str = Field(min_length=1)
    userId: str = Field(min_length=1, max_length=128)
    eventId: str | None = Field(default=None, min_length=16, max_length=64)


async def handle_message(
    *,
    text: str,
    user_id: str,
    event_id: str | None = None,
    audit_dir: Path = Path("var/audit"),
) -> dict[str, Any]:
    """Submit a Feishu-shaped message through the existing kernel pipeline."""
    from ..cli_main import assemble_kernel

    harness = await assemble_kernel(audit_dir=audit_dir)
    echo_script = (
        Path(__file__).resolve().parents[2] / "workers_stub" / "echo_worker.py"
    )
    try:
        await harness.register_worker(
            SimpleNamespace(
                script_path=echo_script,
                expected_capabilities=("echo.say",),
            )
        )
        result = await harness.submit(
            text=text,
            user_id=user_id,
            event_id=event_id,
            source_channel="feishu_stub",
        )
        return {
            "traceId": result.traceId,
            "eventId": result.eventId,
            "traceOutcome": result.traceOutcome,
            "leafOutcomes": list(result.leafOutcomes),
            "audit_event_types": list(result.audit_event_types),
            "duration_s": result.duration_s,
            "message": result.message,
        }
    finally:
        await harness.shutdown()


async def handle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    message = FeishuMessage.model_validate(payload)
    return await handle_message(
        text=message.text,
        user_id=message.userId,
        event_id=message.eventId,
    )


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


__all__ = ["FeishuMessage", "handle_message", "handle_payload", "main"]
