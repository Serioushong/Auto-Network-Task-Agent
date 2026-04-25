from __future__ import annotations

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
        json={"text": "echo hello", "userId": "alice", "eventId": "01KQ1PHASE1000000000000000"},
    )
    assert submit.status_code == 200
    assert submit.json()["message"] == "echo.say"
