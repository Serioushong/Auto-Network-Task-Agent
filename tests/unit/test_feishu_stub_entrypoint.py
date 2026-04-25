from __future__ import annotations

import hashlib
import hmac
import json
import os

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
        "event": {
            "message_id": "om_12345678901234567890",
            "message": {"text": "echo hello"},
            "sender": {"sender_id": "alice"},
        }
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
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}\n{nonce}\n".encode("utf-8") + body, hashlib.sha256).hexdigest()
    headers = {
        "X-Lark-Signature": f"v1={digest}",
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
