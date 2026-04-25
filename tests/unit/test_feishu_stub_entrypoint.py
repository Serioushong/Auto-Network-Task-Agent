from __future__ import annotations

import base64
import hashlib
import json
import os
from Crypto.Cipher import AES
from fastapi.testclient import TestClient

from orchestrator_kernel.entrypoints.feishu_stub import (
    FeishuMessage,
    create_app,
    handle_payload,
)


def test_feishu_message_model_accepts_minimal_payload() -> None:
    msg = FeishuMessage.model_validate(
        {"text": "echo hello", "userId": "alice", "eventId": "01KQ1PHASE1000000000000000"}
    )
    assert msg.text == "echo hello"
    assert msg.userId == "alice"


def test_feishu_message_model_rejects_empty_text() -> None:
    try:
        FeishuMessage.model_validate({"text": "", "userId": "alice"})
    except Exception as exc:  # noqa: BLE001
        assert "text" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected validation failure")


def test_feishu_payload_routes_through_shared_adapter_path() -> None:
    result = __import__("asyncio").run(
        handle_payload({"text": "echo hello", "userId": "alice", "eventId": "01KQ1PHASE1000000000000000"})
    )
    assert result["traceOutcome"] == "feishu-dispatched"
    assert result["message"] == "echo.say"


def test_feishu_http_stub_exposes_submit_and_healthz() -> None:
    client = TestClient(create_app())
    health = client.get("/healthz")
    assert health.status_code == 200
    submit = client.post(
        "/feishu/submit",
        json={"text":"echo hello","userId":"alice","eventId":"01KQ1PHASE1000000000000000"},
    )
    assert submit.status_code == 200
    assert submit.json()["message"] == "echo.say"


def test_feishu_webhook_accepts_feishu_style_payload() -> None:
    client = TestClient(create_app())
    payload = {
        "header": {"event_id": "5e3702a84e847582be8db7fb73283c02"},
        "event": {
            "sender": {
                "sender_id": {
                    "user_id": "e33ggbyz",
                    "open_id": "ou_84aad35d084aa403a838cf73ee18467",
                    "union_id": "on_8ed6aa67826108097d9ee143816345",
                },
                "sender_type": "user",
                "tenant_key": "736588c9260f175e",
            },
            "message": {
                "message_id": "om_5ce6d572455d361153b7cb51da133945",
                "message_type": "text",
                "content": '{"text":"echo hello"}',
            },
        },
    }
    resp = client.post("/feishu/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["message"] == "echo.say"


def test_feishu_token_guard_rejects_invalid_token() -> None:
    client = TestClient(create_app(expected_token="secret"))
    payload = {"text": "echo hello", "userId": "alice", "eventId": "01KQ1PHASE1000000000000000"}
    resp = client.post("/feishu/webhook", json=payload, headers={"X-Feishu-Token": "bad-token"})
    assert resp.status_code == 401


def test_feishu_signature_guard_accepts_valid_signature() -> None:
    secret = "app-secret"
    client = TestClient(create_app())
    payload = {
        "event": {
            "message_id": "om_12345678901234567890",
            "message": {"text": "echo hello"},
            "sender": {"sender_id": "alice"},
        }
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = "1710000000"
    nonce = "nonce-123"
    digest = hashlib.sha256((timestamp + nonce + secret).encode("utf-8") + body).hexdigest()
    headers = {
        "X-Lark-Signature": digest,
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "Content-Type": "application/json",
    }
    previous = os.environ.get("FEISHU_ENCRYPT_KEY")
    os.environ["FEISHU_ENCRYPT_KEY"] = secret
    try:
        resp = client.post("/feishu/webhook", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "echo.say"
    finally:
        if previous is None:
            os.environ.pop("FEISHU_ENCRYPT_KEY", None)
        else:
            os.environ["FEISHU_ENCRYPT_KEY"] = previous


def test_challenge_verification_echoes_challenge_immediately() -> None:
    client = TestClient(create_app())
    resp = client.post("/feishu/webhook", json={"CHALLENGE": "abc123"})
    assert resp.status_code == 200
    assert resp.json() == {"CHALLENGE": "abc123"}


def test_encrypted_payload_can_be_decrypted_and_challenge_echoed() -> None:
    key = "test key"
    plaintext = json.dumps({"CHALLENGE": "abc123"}, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    iv = b"0123456789abcdef"
    pad_len = AES.block_size - (len(plaintext) % AES.block_size)
    padded = plaintext + bytes([pad_len]) * pad_len
    encrypted = iv + AES.new(digest, AES.MODE_CBC, iv).encrypt(padded)
    payload = {"encrypt": base64.b64encode(encrypted).decode("utf-8")}
    previous = os.environ.get("FEISHU_ENCRYPT_KEY")
    os.environ["FEISHU_ENCRYPT_KEY"] = key
    try:
        client = TestClient(create_app())
        resp = client.post("/feishu/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"CHALLENGE": "abc123"}
    finally:
        if previous is None:
            os.environ.pop("FEISHU_ENCRYPT_KEY", None)
        else:
            os.environ["FEISHU_ENCRYPT_KEY"] = previous
