"""T097 — HTTP entry stub integration tests (FR-003 / FR-031 / FR-027).

Exercises ``orchestrator_kernel.entrypoints.http`` end-to-end:

* The `POST /submit` route forwards through the SAME intake pipeline as the
  Typer CLI, so the Phase 9 input guardrails (payload size, rate limiting,
  HIGH_RISK approval) are inherited automatically.
* `sourceChannel="http"` is stamped on the EntryEvent and the audit
  `event_received` line — proving FR-003's "at least one local entry"
  requirement is satisfied beyond the CLI.
* Error paths (oversize body, malformed JSON, missing fields) return
  structured JSON with appropriate HTTP status codes.

Why httpx + ASGITransport rather than uvicorn + a TCP port?
- The kernel is async; spinning a uvicorn server inside pytest serialises
  poorly with the existing event loop.
- httpx.ASGITransport speaks the ASGI protocol directly against the
  FastAPI app, which is exactly what the kernel will see in production
  too. No port-allocation flakiness, no shutdown races.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def http_kernel(tmp_audit_dir: Path) -> AsyncIterator[Any]:
    """Assemble a fresh kernel + an HTTP app wrapper, register echo worker."""
    from orchestrator_kernel.cli_main import assemble_kernel
    from orchestrator_kernel.entrypoints.http import create_app

    harness = await assemble_kernel(audit_dir=tmp_audit_dir)
    echo_script = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "echo_worker.py"
    )
    await harness.register_worker(
        SimpleNamespace(script_path=echo_script, expected_capabilities=("echo.say",))
    )

    app = create_app(harness)
    try:
        yield SimpleNamespace(harness=harness, app=app, audit_dir=tmp_audit_dir)
    finally:
        await harness.shutdown()


def _read_audit_lines(audit_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for jf in sorted(audit_dir.glob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------------
# Happy path — HTTP submit lands a complete trace, sourceChannel=http
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_http_submit_runs_echo_trace_to_succeeded(http_kernel: Any) -> None:
    """POST /submit echo runs the full pipeline, audit shows sourceChannel=http."""
    import httpx
    from httpx import ASGITransport

    transport = ASGITransport(app=http_kernel.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://kernel.local"
    ) as client:
        resp = await client.post(
            "/submit",
            json={
                "text": "echo hello-from-http",
                "userId": "http-user",
            },
            timeout=10.0,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["traceOutcome"] == "all_succeeded", body
    assert body["traceId"], "missing traceId on success"
    assert body["eventId"], "missing eventId on success"
    assert body["duration_s"] >= 0.0
    assert body["leafOutcomes"] == ["succeeded"], body

    rows = _read_audit_lines(http_kernel.audit_dir)
    received = [r for r in rows if r.get("eventType") == "event_received"]
    assert received, "expected at least one event_received audit row"
    # The HTTP entry MUST stamp sourceChannel=http on the audit row's extra.
    assert any(
        r.get("extra", {}).get("sourceChannel") == "http" for r in received
    ), f"no event_received row carried sourceChannel=http; rows={received!r}"


# --------------------------------------------------------------------------
# Idempotency — explicit eventId replays return the cached result
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_http_submit_with_explicit_event_id_is_idempotent(
    http_kernel: Any,
) -> None:
    """Same (userId, eventId) twice ⇒ second response is an idempotent replay."""
    import httpx
    from httpx import ASGITransport

    payload = {
        "text": "echo idem-test",
        "userId": "http-user",
        "eventId": "01HXTEST000IDEMHTTP00",
    }
    transport = ASGITransport(app=http_kernel.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://kernel.local"
    ) as client:
        first = await client.post("/submit", json=payload, timeout=10.0)
        second = await client.post("/submit", json=payload, timeout=10.0)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    a, b = first.json(), second.json()
    assert a["traceId"] == b["traceId"], "idempotent replay must reuse traceId"
    assert b["traceOutcome"] in {"all_succeeded", "idempotent_replay"}, b
    # Audit log MUST include exactly one trace_created (the original) — the
    # replay path emits idempotent_replay instead.
    rows = _read_audit_lines(http_kernel.audit_dir)
    trace_created = [r for r in rows if r.get("eventType") == "trace_created"]
    idem = [r for r in rows if r.get("eventType") == "idempotent_replay"]
    assert len(trace_created) == 1, trace_created
    assert len(idem) == 1, idem


# --------------------------------------------------------------------------
# Phase 9 inheritance — payload size guard MUST trip on oversize body
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_http_submit_oversize_payload_is_rejected(http_kernel: Any) -> None:
    """An oversize text MUST be rejected with HTTP 413 + audit too_large row."""
    import httpx
    from httpx import ASGITransport

    huge = "A" * (32 * 1024)  # 32 KB > 16 KB default cap
    transport = ASGITransport(app=http_kernel.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://kernel.local"
    ) as client:
        resp = await client.post(
            "/submit",
            json={"text": huge, "userId": "http-user"},
            timeout=5.0,
        )

    assert resp.status_code == 413, (resp.status_code, resp.text)
    body = resp.json()
    assert body["traceOutcome"] == "rejected", body
    assert "payload too large" in body.get("message", ""), body

    rows = _read_audit_lines(http_kernel.audit_dir)
    too_large = [r for r in rows if r.get("eventType") == "event_rejected_too_large"]
    assert too_large, "expected event_rejected_too_large audit row"
    # No trace should have been created.
    assert not [r for r in rows if r.get("eventType") == "trace_created"], rows


# --------------------------------------------------------------------------
# Schema errors — malformed body returns 400 with JSON error envelope
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_http_submit_missing_text_field_returns_400(
    http_kernel: Any,
) -> None:
    """A request missing the `text` field MUST return HTTP 400, not 500."""
    import httpx
    from httpx import ASGITransport

    transport = ASGITransport(app=http_kernel.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://kernel.local"
    ) as client:
        resp = await client.post(
            "/submit",
            json={"userId": "http-user"},  # text missing
            timeout=5.0,
        )

    assert resp.status_code in {400, 422}, (resp.status_code, resp.text)
    body = resp.json()
    # FastAPI's default 422 envelope OR our custom 400 envelope — both
    # MUST mention the missing field somewhere parseable.
    rendered = json.dumps(body)
    assert "text" in rendered, body


# --------------------------------------------------------------------------
# Channel surface — /healthz responds 200 even on a freshly-built app
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_http_healthz_reports_kernel_ready(http_kernel: Any) -> None:
    """A liveness route is required for any HTTP entry stub."""
    import httpx
    from httpx import ASGITransport

    transport = ASGITransport(app=http_kernel.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://kernel.local"
    ) as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ready") is True, body
    assert body.get("sourceChannel") == "http", body
