"""T037 — Phase 3 US1 RED: no_capable_worker failure path.

Covers FR-008: 'dispatched only to a worker declaring the capability; no
match -> failed(reason=no_capable_worker)'.

Scenario: submit an event whose LLM-planned capability is `desktop.click`,
but only `echo.say` is registered. The trace MUST:
- reach a terminal state `failed(no_capable_worker)`;
- record `task_failed` in the audit log, with `failureReason='no_capable_worker'`;
- deliver a ResultSummary via the source channel (FR-029) with
  traceOutcome in {all_failed}.

Today this fails because `assemble_kernel` and the dispatcher do not exist
yet (T040 / T045).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.mark.integration
async def test_missing_capability_yields_no_capable_worker(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
) -> None:
    """Only echo.say is registered; a desktop.click intent must fail cleanly."""
    await kernel_harness.register_worker(echo_worker_spec)

    result = await kernel_harness.submit(
        text="click 100,200",
        user_id="alice",
        timeout_s=3.0,
    )

    assert result.traceOutcome == "all_failed"
    assert result.leafOutcomes == ["failed"]


@pytest.mark.integration
async def test_no_capable_worker_audit_shape(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Audit log must carry a task_failed event with failureReason=no_capable_worker."""
    await kernel_harness.register_worker(echo_worker_spec)
    await kernel_harness.submit(text="click 100,200", user_id="alice")

    events = audit_events_factory()
    task_failed = [e for e in events if e.get("eventType") == "task_failed"]
    assert task_failed, "expected at least one task_failed audit event"

    match = [
        e
        for e in task_failed
        if (e.get("extra") or {}).get("failureReason") == "no_capable_worker"
        or (e.get("failureReason") == "no_capable_worker")
    ]
    assert match, (
        f"expected failureReason=no_capable_worker on task_failed, got {task_failed}"
    )

    types = [e["eventType"] for e in events]
    assert "result_summary_delivered" in types, (
        "user must still receive a result summary on failure (FR-029)"
    )


@pytest.mark.integration
async def test_no_workers_registered_is_also_no_capable_worker(
    kernel_harness: KernelHarnessProtocol,
) -> None:
    """Zero workers registered -> every intent fails with no_capable_worker."""
    result = await kernel_harness.submit(
        text="echo hello",
        user_id="alice",
        timeout_s=3.0,
    )

    assert result.traceOutcome == "all_failed"
    assert result.leafOutcomes == ["failed"]
