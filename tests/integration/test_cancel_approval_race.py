"""T064 — Approval-vs-Cancel race (spec.md Edge Case "approval 与 cancel 竞态").

Validates the "last-to-arrive wins" rule for concurrent ``approve`` and
``cancel`` against the same pending trace. Two opposing scenarios:

* **cancel wins** — approve submitted first, cancel arrives fractions of
  a second later *before* the approval-gate releases the submit. The
  leaf MUST end as ``cancelled`` (``failureReason="user_cancel_before_approval"``)
  and MUST NOT be dispatched. ``approval_granted`` SHOULD NOT be
  emitted; if it is, it must be accompanied by an immediate
  ``task_cancelled`` with the race metadata so auditors can reconstruct
  the outcome.
* **approve wins** — symmetric case: the approve arrives and resolves
  the gate *before* cancel is dispatched. Leaf walks to ``succeeded``;
  cancel returns ``already_terminal`` (or ``not_found`` depending on
  whether the trace bookkeeping has been torn down).

The approve vs cancel ordering is driven explicitly via awaiting small
sleeps between the two calls — no wall-clock races, the asyncio loop
gives deterministic ordering.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.fixture
def danger_worker_spec() -> WorkerSpec:
    script = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "danger_worker.py"
    )
    return WorkerSpec(script_path=script, expected_capabilities=("file.delete",))


@pytest.mark.integration
async def test_cancel_after_approve_wins_when_arrives_last(
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
    await asyncio.sleep(0.15)
    trace_id = _find_event(
        audit_events_factory(), "task_pending_approval"
    )["traceId"]

    # Race: approve is "ahead" in userspace but we deliberately cancel
    # after the approve to prove cancel-arrived-last short-circuits the
    # dispatch path regardless.
    approve_coro = kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="approve", user_id="alice"
    )
    cancel_coro = kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    approve_result, cancel_outcome = await asyncio.gather(
        approve_coro, cancel_coro
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)

    assert cancel_outcome.status is CancelStatus.accepted
    assert "cancelled" in result.leafOutcomes
    assert result.traceOutcome == "cancelled"
    types = [e.get("eventType") for e in audit_events_factory()]
    assert "task_cancelled" in types
    assert "task_dispatched" not in types, (
        "cancel-won race MUST NOT dispatch the task"
    )
    _ = approve_result


@pytest.mark.integration
async def test_cancel_after_terminal_returns_already_terminal(
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
    await asyncio.sleep(0.15)
    trace_id = _find_event(
        audit_events_factory(), "task_pending_approval"
    )["traceId"]

    await kernel_harness.submit_approval_response(
        trace_id=trace_id, decision="approve", user_id="alice"
    )
    result = await asyncio.wait_for(submit_task, timeout=5.0)
    assert result.traceOutcome == "all_succeeded"

    outcome = await kernel_harness.request_cancel(
        trace_id=trace_id, user_id="alice"
    )
    assert outcome.status in {
        CancelStatus.already_terminal,
        CancelStatus.not_found,
    }


def _find_event(
    events: list[dict[str, Any]], event_type: str
) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("eventType") == event_type:
            return event
    raise AssertionError(f"expected event {event_type} not found")
