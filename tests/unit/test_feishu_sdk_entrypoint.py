from __future__ import annotations

from types import SimpleNamespace

from orchestrator_kernel.entrypoints.feishu_sdk import _message_event_to_payload


def test_message_event_to_payload_maps_text_user_and_event_id() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="evt_123"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om_456",
                content='{"text":"echo hello"}',
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(
                    user_id="user_1",
                    open_id="open_1",
                    union_id="union_1",
                )
            ),
        ),
    )

    payload = _message_event_to_payload(data)

    assert payload == {
        "text": "echo hello",
        "userId": "user_1",
        "eventId": "evt_123",
    }


def test_message_event_to_payload_falls_back_to_open_id_when_user_id_missing() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(event_id="evt_123"),
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om_456",
                content='{"text":"echo hello"}',
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(
                    user_id=None,
                    open_id="open_1",
                    union_id="union_1",
                )
            ),
        ),
    )

    payload = _message_event_to_payload(data)

    assert payload["userId"] == "open_1"
