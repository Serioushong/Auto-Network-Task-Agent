"""T055 — Phase 5 US3 RED: HIGH_RISK approval-gate integration.

Covers spec.md §P3 Acceptance Scenarios:

1. **Scenario 1 — pending_approval**: submit ``file.delete`` → kernel
   emits ``approval_request``; Task sits in ``pending_approval``; no
   dispatch yet.
2. **Scenario 2 — approve**: after submit, send ``approve`` → Task
   dispatched → succeeded; audit chain contains ``approval_granted``.
3. **Scenario 3 — timeout**: after submit, never respond → Task
   transitions to ``denied_by_timeout``; audit contains
   ``approval_timeout``.
4. **Scenario 4 — deny**: after submit, send ``deny`` → Task →
   ``denied``; audit contains ``approval_denied``.
5. **Edge Case — impersonation**: foreign userId attempts to approve →
   Task stays pending; audit contains ``approval_impersonation_rejected``;
   legitimate user can still approve afterwards.

The harness surface used here is contractual for Phase 5:
``harness.submit_approval_response(trace_id, decision, user_id)`` and
the ``approval_timeout_ms=<int>`` kwarg on ``assemble_kernel``. Neither
exists today — that's the RED signal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from ._harness import KernelHarnessProtocol, WorkerSpec

# --- module-local fixtures --------------------------------------------------


@pytest.fixture
def danger_worker_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "danger_worker.py"
    )


@pytest.fixture
def danger_worker_spec(danger_worker_script: Path) -> WorkerSpec:
    return WorkerSpec(
        script_path=danger_worker_script,
        expected_capabilities=("file.delete",),
    )


@pytest_asyncio.fixture
async def fast_timeout_harness(tmp_audit_dir: Path) -> Any:
    """Short approval window so timeout tests don't block 10 minutes."""
    from orchestrator_kernel.cli_main import assemble_kernel  # type: ignore[attr-defined]

    harness = await assemble_kernel(
        audit_dir=tmp_audit_dir, approval_timeout_ms=500
    )
    try:
        yield harness
    finally:
        await harness.shutdown()


# --- Scenario 1 — pending_approval blocks dispatch --------------------------


@pytest.mark.integration
async def test_high_risk_submit_enters_pending_approval(
    kernel_harness: KernelHarnessProtocol,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    await kernel_harness.register_worker(danger_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(text="delete fake.txt", user_id="alice")
    )
    try:
        await asyncio.sleep(0.2)
        assert not submit_task.done(), (
            "submit must block waiting for approval, not auto-dispatch"
        )

        events = audit_events_factory()
        pending_events = [
            e for e in events
            if e.get("eventType") == "task_pending_approval"
        ]
        dispatched_events = [
            e for e in events if e.get("eventType") == "task_dispatched"
        ]
        assert len(pending_events) == 1
        assert dispatched_events == [], (
            "HIGH_RISK must NOT be dispatched before approval"
        )
    finally:
        submit_task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await submit_task


# --- Scenario 2 — approve dispatches and completes -------------------------


@pytest.mark.integration
async def test_approve_leads_to_succeeded(
    kernel_harness: KernelHarnessProtocol,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    await kernel_harness.register_worker(danger_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(text="delete fake.txt", user_id="alice")
    )
    await asyncio.sleep(0.1)

    pending = _latest_pending(audit_events_factory())
    trace_id = pending["traceId"]

    await kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="approve", user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)

    assert result.traceOutcome == "all_succeeded"
    types = {e.get("eventType") for e in audit_events_factory()}
    assert "approval_granted" in types
    assert "task_dispatched" in types
    assert "task_succeeded" in types


# --- Scenario 3 — timeout → denied_by_timeout ------------------------------


@pytest.mark.integration
async def test_approval_timeout_denies_task(
    fast_timeout_harness: Any,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    harness = fast_timeout_harness
    await harness.register_worker(danger_worker_spec)

    result = await asyncio.wait_for(
        harness.submit(text="delete fake.txt", user_id="alice"), timeout=5.0
    )

    assert result.traceOutcome in {"all_failed", "denied"}
    assert "denied_by_timeout" in result.leafOutcomes

    events = audit_events_factory()
    types = [e.get("eventType") for e in events]
    assert "approval_timeout" in types
    assert "task_dispatched" not in types, (
        "timed-out task MUST NOT be dispatched"
    )


# --- Scenario 4 — deny ------------------------------------------------------


@pytest.mark.integration
async def test_deny_leads_to_denied_outcome(
    kernel_harness: KernelHarnessProtocol,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    await kernel_harness.register_worker(danger_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(text="delete fake.txt", user_id="alice")
    )
    await asyncio.sleep(0.1)
    pending = _latest_pending(audit_events_factory())
    trace_id = pending["traceId"]

    await kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="deny", user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)

    assert "denied" in result.leafOutcomes
    types = [e.get("eventType") for e in audit_events_factory()]
    assert "approval_denied" in types
    assert "task_dispatched" not in types


# --- Edge Case — impersonation_rejected ------------------------------------


@pytest.mark.integration
async def test_foreign_user_cannot_approve(
    kernel_harness: KernelHarnessProtocol,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    await kernel_harness.register_worker(danger_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(text="delete fake.txt", user_id="alice")
    )
    await asyncio.sleep(0.1)
    pending = _latest_pending(audit_events_factory())
    trace_id = pending["traceId"]

    await kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="approve", user_id="mallory"
    )
    await asyncio.sleep(0.1)
    assert not submit_task.done(), (
        "impersonation must not release the pending submit"
    )

    types_before = [e.get("eventType") for e in audit_events_factory()]
    assert "approval_impersonation_rejected" in types_before
    assert "approval_granted" not in types_before

    await kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="approve", user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)
    assert result.traceOutcome == "all_succeeded"


def _latest_pending(events: list[dict[str, Any]]) -> dict[str, Any]:
    pendings = [
        e for e in events if e.get("eventType") == "task_pending_approval"
    ]
    assert pendings, "expected at least one task_pending_approval event"
    return pendings[-1]
