"""T097 — Local HTTP entry stub (FR-003 / FR-031 / FR-027).

Phase 10 wiring note:
- HTTP can now be routed through the Phase 10 main-agent adapter path.
- If Phase 10 adapter wiring is not available, the app falls back to the
  existing KernelHarness path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Body, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..phase10_entrypoints import Phase10EntrypointAdapter

if TYPE_CHECKING:
    from ..cli_main import KernelHarness, TraceResult

_DEFAULT_TIMEOUT_S = 30.0


class _SubmitRequest(BaseModel):
    """Wire schema for ``POST /submit`` body. Mirrors EntryEvent's user surface."""

    text: str = Field(min_length=1)
    userId: str = Field(min_length=1, max_length=128, default="http-user")
    eventId: str | None = Field(default=None, min_length=16, max_length=64)
    timeoutS: float = Field(default=_DEFAULT_TIMEOUT_S, gt=0.0, le=600.0)


class _SubmitResponse(BaseModel):
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
    if message.startswith("payload too large"):
        return 413
    if message.startswith("rate_limited"):
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_200_OK


def create_app(
    harness: KernelHarness,
    *,
    phase10_adapter: Phase10EntrypointAdapter | None = None,
) -> FastAPI:
    """Build a FastAPI app bound to the given ``KernelHarness``.

    Args:
        harness: An already-assembled kernel (call ``assemble_kernel(...)`` first).
        phase10_adapter: Optional Phase 10 main-agent adapter. When supplied,
            ``POST /submit`` uses it first and falls back to the kernel harness
            on adapter errors.
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
    async def submit(body: Annotated[_SubmitRequest, Body(...)]) -> Any:
        if phase10_adapter is not None:
            try:
                phase10_result = phase10_adapter.submit(
                    text=body.text,
                    user_id=body.userId,
                    event_id=body.eventId,
                    source_channel="http",
                )
                return _SubmitResponse(
                    traceId=phase10_result.trace_id,
                    eventId=phase10_result.event_id or body.eventId or "",
                    traceOutcome="phase10-dispatched",
                    leafOutcomes=[],
                    audit_event_types=[],
                    duration_s=0.0,
                    message=phase10_result.selected_capability,
                )
            except Exception:
                # Compatibility fallback: keep the existing kernel path alive.
                pass

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
                return JSONResponse(
                    status_code=code, content=response.model_dump(mode="json")
                )
        return response

    return app


__all__ = ["create_app"]
