"""T062 — Phase 6 US4 RED: `cancel <traceId>` integration.

Covers spec.md §P4 Acceptance Scenarios + the "repeat-cancel" edge case:

1. **Scenario 1 — running task**: submit a long ``sleep.wait`` leaf →
   once the worker is running, send ``cancel`` → Task walks to
   ``cancelled`` within ≤ 5 s (SC-004); audit chain contains
   ``cancel_requested`` + ``soft_abort_sent`` + ``task_cancelled``.
2. **Scenario 2 — pending_approval**: submit a HIGH_RISK ``delete`` leaf
   that parks in ``pending_approval`` → cancel → Task → ``cancelled``
   with ``user_cancel_before_approval`` reason; no ``approval_*`` audit
   event is emitted.
3. **Scenario 3 — not_found**: cancel a trace that never existed → the
   kernel returns ``not_found`` (``CancelStatus.not_found``) and audits
   ``cancel_not_found``; no ``task_cancelled`` event is written.
4. **Edge Case — repeat cancel**: cancel the same trace twice; the first
   call returns ``accepted``, the second returns ``already_cancelled``;
   ``soft_abort_sent`` appears exactly once (no duplicate signal to the
   worker, per spec.md Edge Case "重复 cancel").

Contract additions under test:
* ``KernelHarness.request_cancel(trace_id, user_id) -> CancelOutcome``
* ``CancelOutcome.status`` enum with values
  ``accepted | not_found | already_cancelled``.
* New AuditEventTypes emitted: ``cancel_requested`` /
  ``soft_abort_sent`` / ``task_cancelled`` / ``cancel_not_found``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.fixture
def sleep_worker_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "sleep_worker.py"
    )


@pytest.fixture
def sleep_worker_spec(sleep_worker_script: Path) -> WorkerSpec:
    return WorkerSpec(
        script_path=sleep_worker_script,
        expected_capabilities=("sleep.wait",),
    )


@pytest.fixture
def danger_worker_spec() -> WorkerSpec:
    script = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "danger_worker.py"
    )
    return WorkerSpec(script_path=script, expected_capabilities=("file.delete",))


# --- Scenario 1 — running task cancels within 5s ---------------------------


@pytest.mark.integration
async def test_cancel_running_task_within_budget(
    kernel_harness: KernelHarnessProtocol,
    sleep_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    from orchestrator_kernel.kernel.cancel import CancelStatus

    await kernel_harness.register_worker(sleep_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(
            text="sleep 10", user_id="alice", timeout_s=15.0
        )
    )
    trace_id = await _await_trace_id(audit_events_factory)
    await asyncio.sleep(0.3)  # let the worker log `started`

    started = asyncio.get_running_loop().time()
    outcome = await kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=6.0)
    elapsed = asyncio.get_running_loop().time() - started

    assert outcome.status is CancelStatus.accepted
    assert elapsed <= 5.0, f"FR-013 violated: cancel took {elapsed:.2f}s > 5s"
    assert result.traceOutcome == "cancelled"
    assert result.leafOutcomes == ["cancelled"]

    types = [e.get("eventType") for e in audit_events_factory()]
    assert "cancel_requested" in types
    assert "soft_abort_sent" in types
    assert "task_cancelled" in types


# --- Scenario 2 — pending_approval cancels before approval -----------------


@pytest.mark.integration
async def test_cancel_pending_approval_task(
    kernel_harness: KernelHarnessProtocol,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    from orchestrator_kernel.kernel.cancel import CancelStatus

    await kernel_harness.register_worker(danger_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(
            text="delete fake.txt", user_id="alice", timeout_s=30.0
        )
    )
    await asyncio.sleep(0.2)
    trace_id = _find_event(
        audit_events_factory(), "task_pending_approval"
    )["traceId"]

    outcome = await kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)

    assert outcome.status is CancelStatus.accepted
    assert "cancelled" in result.leafOutcomes

    events = audit_events_factory()
    types = [e.get("eventType") for e in events]
    assert "cancel_requested" in types
    assert "task_cancelled" in types
    cancel_task_event = _find_event(events, "task_cancelled")
    assert cancel_task_event.get("extra", {}).get(
        "failureReason"
    ) == "user_cancel_before_approval"
    assert "approval_granted" not in types
    assert "approval_denied" not in types


# --- Scenario 3 — cancel unknown trace -------------------------------------


@pytest.mark.integration
async def test_cancel_unknown_trace_returns_not_found(
    kernel_harness: KernelHarnessProtocol,
    sleep_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    from orchestrator_kernel.kernel.cancel import CancelStatus

    await kernel_harness.register_worker(sleep_worker_spec)
    outcome = await kernel_harness.request_cancel(
        trace_id="01TRACENONEXISTENTXXXXXXXX", user_id="alice"
    )
    assert outcome.status is CancelStatus.not_found

    types = [e.get("eventType") for e in audit_events_factory()]
    assert "cancel_not_found" in types
    assert "task_cancelled" not in types


# --- Edge Case — repeat cancel --------------------------------------------


@pytest.mark.integration
async def test_repeat_cancel_returns_already_cancelled(
    kernel_harness: KernelHarnessProtocol,
    sleep_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    from orchestrator_kernel.kernel.cancel import CancelStatus

    await kernel_harness.register_worker(sleep_worker_spec)

    submit_task = asyncio.create_task(
        kernel_harness.submit(
            text="sleep 10", user_id="alice", timeout_s=15.0
        )
    )
    trace_id = await _await_trace_id(audit_events_factory)
    await asyncio.sleep(0.3)

    first = await kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    second = await kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    await asyncio.wait_for(submit_task, timeout=6.0)

    assert first.status is CancelStatus.accepted
    assert second.status is CancelStatus.already_cancelled

    types = [e.get("eventType") for e in audit_events_factory()]
    assert types.count("soft_abort_sent") == 1, (
        "repeat cancel MUST NOT re-send soft-abort"
    )
    assert types.count("cancel_requested") >= 1


# --- helpers ---------------------------------------------------------------


async def _await_trace_id(
    audit_events_factory: Callable[[], list[dict[str, Any]]],
    *,
    timeout_s: float = 2.0,
) -> str:
    """Poll audit for the first ``trace_created`` event and return its traceId."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        for event in audit_events_factory():
            if event.get("eventType") == "trace_created":
                return str(event["traceId"])
        await asyncio.sleep(0.05)
    raise AssertionError("trace_created not audited within timeout")


def _find_event(
    events: list[dict[str, Any]], event_type: str
) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("eventType") == event_type:
            return event
    raise AssertionError(f"expected event {event_type} not found")
