"""T097 — Local HTTP entry stub (FR-003 / FR-031 / FR-027).

A thin FastAPI wrapper that lets a local operator POST events into the
**same** intake pipeline the Typer CLI uses. The Phase 9 input guardrails
(payload size guard, four-dimensional rate limiter, HIGH_RISK approval
gate) are inherited automatically because every request is funnelled
through ``KernelHarness.submit(source_channel="http", ...)``.

Why a dedicated module rather than embedding inside ``cli_main``:

* The HTTP surface is optional. The orchestrator-kernel CLI must remain
  importable on machines without ``fastapi`` installed at runtime (it's a
  declared dependency now, but the principle stands for any future
  ``feishu_stub`` / ``slack_stub`` channel).
* The factory function ``create_app(harness)`` accepts an already-built
  ``KernelHarness`` so tests can inject a temp-dir audit / rate-limited
  / single-worker harness without spinning a subprocess.

Routes:

| Method | Path     | Purpose                                        |
| ------ | -------- | ---------------------------------------------- |
| POST   | /submit  | Queue a single event; returns the TraceResult  |
| GET    | /healthz | Liveness probe; returns ``{"ready": True}``    |

Status-code policy:

* ``200`` — kernel processed the event (succeeded / failed / rejected
  by approval / cancelled). The full ``TraceResult`` JSON is the body.
* ``413`` — payload exceeded the configured byte cap (FR-031). Body
  carries ``traceOutcome="rejected"`` and a human-readable ``message``.
* ``429`` — rate-limited (FR-025/026/027). Body carries the rejecting
  ``dimension`` so operators can re-tune the right counter.
* ``400`` / ``422`` — schema-level rejection from EntryEvent. FastAPI's
  default validator already raises 422 on Pydantic errors; we catch the
  kernel's ``ValidationError`` paths and remap to 400 for symmetry with
  the CLI's exit-code 2 behaviour.

Production hardening notes (intentionally **out of scope** for the
MVP stub, listed here so reviewers know where the gaps are):

* No auth — operator-local only. Real deployment requires the daemon-mode
  socket (Phase 7 T084+) or a reverse proxy.
* No streaming; ``/submit`` is request-response. Long-running HIGH_RISK
  approvals are bounded by ``approval_timeout_ms`` on the harness.
* ``register_worker`` etc. are NOT exposed — workers are wired by the
  process that called ``assemble_kernel``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Body, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from ..cli_main import KernelHarness, TraceResult

_DEFAULT_TIMEOUT_S = 30.0


class _SubmitRequest(BaseModel):
    """Wire schema for ``POST /submit`` body. Mirrors EntryEvent's user surface.

    ``eventId`` is optional — the kernel auto-generates a ULID-26 if absent.
    ``timeoutS`` overrides the per-request dispatch wait (default 30s for
    HTTP since the CLI demo timeout of 5s is too tight for HIGH_RISK
    approval flows).
    """

    text: str = Field(min_length=1)
    userId: str = Field(min_length=1, max_length=128, default="http-user")
    eventId: str | None = Field(default=None, min_length=16, max_length=64)
    timeoutS: float = Field(default=_DEFAULT_TIMEOUT_S, gt=0.0, le=600.0)


class _SubmitResponse(BaseModel):
    """Wire schema for ``POST /submit`` response. Mirrors ``TraceResult``."""

    traceId: str
    eventId: str
    traceOutcome: str
    leafOutcomes: list[str] = Field(default_factory=list)
    audit_event_types: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    message: str = ""


def _trace_result_to_response(result: TraceResult) -> _SubmitResponse:
    return _SubmitResponse(
        traceId=result.traceId,
        eventId=result.eventId,
        traceOutcome=result.traceOutcome,
        leafOutcomes=list(result.leafOutcomes),
        audit_event_types=list(result.audit_event_types),
        duration_s=result.duration_s,
        message=result.message,
    )


def _status_code_for_rejection(message: str) -> int:
    """Map a rejection message to the right HTTP status code.

    Uses message-prefix matching because the kernel's TraceResult does not
    carry a structured ``rejection_reason`` field today (that would belong
    in a future contract bump). The strings here are stable — they're
    asserted on by Phase 9 integration tests — so this mapping is safe.
    """
    if message.startswith("payload too large"):
        return 413
    if message.startswith("rate_limited"):
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_200_OK


def create_app(harness: KernelHarness) -> FastAPI:
    """Build a FastAPI app bound to the given ``KernelHarness``.

    Args:
        harness: An already-assembled kernel (call ``assemble_kernel(...)``
            first). Workers MUST already be registered — ``/submit`` does
            not bootstrap the kernel itself.

    Returns:
        FastAPI application with ``POST /submit`` and ``GET /healthz``
        attached. Safe to mount under any prefix via ``app.mount(...)``.
    """
    app = FastAPI(
        title="Orchestrator Kernel HTTP Entry (MVP stub)",
        version="0.1.0",
        description=(
            "Local HTTP channel for the orchestrator-kernel. "
            "Inherits all Phase 9 input guardrails. "
            "NOT for production — see entrypoints/http.py docstring for gaps."
        ),
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ready": getattr(harness, "_ready", True),
            "sourceChannel": "http",
            "kernelTitle": app.title,
        }

    @app.post(
        "/submit",
        response_model=_SubmitResponse,
        responses={
            413: {"description": "Payload exceeded the configured byte cap (FR-031)"},
            429: {"description": "Rate-limited (FR-025/026/027)"},
            400: {"description": "Schema or contract validation error"},
        },
    )
    async def submit(
        body: Annotated[_SubmitRequest, Body(...)],
    ) -> Any:
        try:
            result: TraceResult = await harness.submit(
                text=body.text,
                user_id=body.userId,
                event_id=body.eventId,
                timeout_s=body.timeoutS,
                source_channel="http",
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"errors": exc.errors()},
            ) from exc

        response = _trace_result_to_response(result)
        if result.traceOutcome == "rejected":
            code = _status_code_for_rejection(result.message)
            if code != status.HTTP_200_OK:
                # Return the TraceResult shape verbatim with the rejection
                # status code; callers can json-decode it the same way they
                # do on a 200, no nested ``detail`` envelope to peel.
                return JSONResponse(
                    status_code=code, content=response.model_dump(mode="json")
                )
        return response

    return app


__all__ = ["create_app"]
