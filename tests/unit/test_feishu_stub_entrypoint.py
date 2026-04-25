from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_feishu_stub_handle_message_runs_kernel(tmp_path: Path) -> None:
    from orchestrator_kernel.entrypoints.feishu_stub import handle_message

    result = await handle_message(
        text="echo hello",
        user_id="feishu-user",
        event_id="01KQ1FEISHU0000000000000000",
        audit_dir=tmp_path,
    )

    assert result["traceOutcome"] == "all_succeeded"
    assert result["message"] == "hello"
    assert result["eventId"] == "01KQ1FEISHU0000000000000000"

    audit_files = sorted(tmp_path.glob("*.jsonl"))
    assert audit_files
    audit_text = "\n".join(f.read_text(encoding="utf-8") for f in audit_files)
    assert 'feishu_stub' in audit_text


def test_feishu_stub_rejects_invalid_payload() -> None:
    from orchestrator_kernel.entrypoints.feishu_stub import FeishuMessage

    with pytest.raises(Exception):
        FeishuMessage.model_validate({"text": "", "userId": "x"})
