"""T036 — Phase 3 US1 RED: echo hello end-to-end.

Drives the "basic dispatch loop" MVP (spec.md P1):
    CLI submit -> EntryEvent -> Trace/Task tree -> Dispatch to echo-worker
    -> ResultFrame -> ResultSummary -> CLI delivery

Assertions (all currently RED; GREEN after T039~T047):
1. TraceResult.traceOutcome == "all_succeeded" and the only leaf echoes
   "echo hello" back.
2. Audit chain is complete in the order required by FR-006 / FR-019:
        event_received -> trace_created -> task_created ->
        task_dispatched -> task_started -> task_succeeded ->
        result_summary_prepared -> result_summary_delivered
3. End-to-end duration <= 3 s (spec.md SC-002).

The test calls into the harness facade defined in `tests/integration/
conftest.py`. Failure today stems from `assemble_kernel` not existing in
`cli_main`; once T045 wires the real loop, assertions start running.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec

EXPECTED_AUDIT_CHAIN = [
    "event_received",
    "trace_created",
    "task_created",
    "task_dispatched",
    "task_started",
    "task_succeeded",
    "result_summary_prepared",
    "result_summary_delivered",
]


@pytest.mark.integration
async def test_echo_hello_end_to_end_succeeded(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
) -> None:
    """Echo worker registered, submit 'echo hello', trace finishes succeeded."""
    await kernel_harness.register_worker(echo_worker_spec)

    result = await kernel_harness.submit(
        text="echo hello",
        user_id="alice",
        timeout_s=3.0,
    )

    assert result.traceOutcome == "all_succeeded", (
        f"expected all_succeeded, got {result.traceOutcome!r}"
    )
    assert result.leafOutcomes == ["succeeded"]
    assert "hello" in result.message.lower()


@pytest.mark.integration
async def test_echo_hello_audit_chain_complete(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Audit log MUST record the full event chain in order (FR-006 / FR-019)."""
    await kernel_harness.register_worker(echo_worker_spec)
    await kernel_harness.submit(text="echo hello", user_id="alice")

    events = audit_events_factory()
    types_in_order = [e["eventType"] for e in events]

    missing = [t for t in EXPECTED_AUDIT_CHAIN if t not in types_in_order]
    assert not missing, (
        f"audit chain missing eventTypes {missing}; saw {types_in_order}"
    )

    positions = [types_in_order.index(t) for t in EXPECTED_AUDIT_CHAIN]
    assert positions == sorted(positions), (
        f"audit chain out of order; expected {EXPECTED_AUDIT_CHAIN}, got {types_in_order}"
    )


@pytest.mark.integration
async def test_echo_hello_under_p95_budget(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
) -> None:
    """SC-002: basic dispatch end-to-end p95 <= 3 s."""
    await kernel_harness.register_worker(echo_worker_spec)

    result = await kernel_harness.submit(
        text="echo hello",
        user_id="alice",
        timeout_s=3.0,
    )

    assert result.duration_s <= 3.0, (
        f"end-to-end duration {result.duration_s:.2f}s exceeds 3.0s budget"
    )


@pytest.mark.integration
async def test_trace_and_event_ids_present(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
) -> None:
    """Every successful submit returns non-empty traceId + eventId (FR-001 / FR-004)."""
    await kernel_harness.register_worker(echo_worker_spec)
    result = await kernel_harness.submit(text="echo hello", user_id="alice")

    assert result.traceId and len(result.traceId) >= 16
    assert result.eventId and len(result.eventId) >= 16
