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
- optional SHA256 signature guard
- Feishu-style webhook payload extraction
- encrypted payload decryption when ``encrypt`` is present
- verbose request logging for debugging webhook payloads
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

from .feishu_common import FeishuMessage, handle_payload as shared_handle_payload


class AESCipher:
    def __init__(self, key: str) -> None:
        self.key = hashlib.sha256(key.encode("utf-8")).digest()

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        return data[:-data[-1]]

    def decrypt(self, enc: bytes) -> bytes:
        iv = enc[: AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size :]))

    def decrypt_string(self, enc: str) -> str:
        decoded = base64.b64decode(enc)
        return self.decrypt(decoded).decode("utf-8")



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
    encrypt_key = os.environ.get("FEISHU_ENCRYPT_KEY") or os.environ.get(
        "FEISHU_WEBHOOK_APP_SECRET"
    ) or None
    stub_token = os.environ.get("FEISHU_STUB_TOKEN") or None

    if expected_token:
        if not _constant_time_eq(token_header, expected_token):
            raise HTTPException(status_code=401, detail="invalid webhook verification token")
    elif stub_token:
        if not _constant_time_eq(token_header, stub_token):
            raise HTTPException(status_code=401, detail="invalid stub token")

    if encrypt_key:
        if not (signature_header and timestamp_header and nonce_header):
            raise HTTPException(status_code=401, detail="missing signature headers")
        digest = hashlib.sha256((timestamp_header + nonce_header + encrypt_key).encode("utf-8") + body).hexdigest()
        if not _constant_time_eq(signature_header, digest):
            raise HTTPException(status_code=401, detail="invalid webhook signature")


def _decrypt_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    encrypt_value = payload.get("encrypt")
    if not isinstance(encrypt_value, str):
        return payload
    encrypt_key = os.environ.get("FEISHU_ENCRYPT_KEY") or os.environ.get("FEISHU_WEBHOOK_APP_SECRET")
    if not encrypt_key:
        raise HTTPException(status_code=500, detail="missing FEISHU_ENCRYPT_KEY")
    cipher = AESCipher(encrypt_key)
    decrypted = cipher.decrypt_string(encrypt_value)
    logger.info("feishu_webhook_request decrypted envelope: %s", decrypted)
    print(f"[feishu_webhook_request] decrypted={decrypted}", flush=True)
    try:
        inner = json.loads(decrypted)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid decrypted json") from exc
    if not isinstance(inner, dict):
        raise HTTPException(status_code=400, detail="decrypted payload must be a JSON object")
    return inner


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
    return await shared_handle_payload(payload)


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
        print(
            f"[feishu_webhook_request] path={request.url.path} body={raw_body.decode('utf-8', errors='replace')} headers={headers_snapshot}",
            flush=True,
        )
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

        if isinstance(payload, dict) and "encrypt" in payload:
            payload = _decrypt_envelope(payload)

        if isinstance(payload, dict):
            challenge = None
            if isinstance(payload.get("CHALLENGE"), str):
                challenge = payload["CHALLENGE"]
            elif isinstance(payload.get("challenge"), str):
                challenge = payload["challenge"]
            if challenge is not None:
                print(
                    f"[feishu_webhook_request] challenge_echo path={request.url.path} challenge={challenge}",
                    flush=True,
                )
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
