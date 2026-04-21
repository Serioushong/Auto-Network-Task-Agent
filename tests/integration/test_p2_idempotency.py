"""T048 — Phase 4 US2 RED: integration tests for idempotent submit.

Covers the three Acceptance Scenarios from spec.md §P2:

1. **已完成终态重投**: submit E2 → wait terminal → 4 more submits with
   eventId=E2 → same traceId each time, exactly one dispatch, 4
   ``idempotent_replay`` audit events.
2. **running 期间重投**: 3 concurrent submits with the same eventId at
   the same moment → same traceId × 3, exactly one ``task_dispatched``.
3. **已失败重投**: submit a desktop.click intent (no worker) → get
   ``all_failed`` → resubmit same eventId → same traceId; no second
   ``task_failed`` event because the original failed trace is replayed.

Today RED because ``IdempotencyCache`` does not exist yet (T051) and the
harness does not consult it (T052). The tests exercise the full
``KernelHarness`` so T052 wiring is validated end-to-end once GREEN.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec

E2 = "E2AAAAAAAAAAAAAAAA"
E3 = "E3AAAAAAAAAAAAAAAA"
E4 = "E4AAAAAAAAAAAAAAAA"


@pytest.mark.integration
async def test_idempotent_replay_after_terminal(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Spec §P2 Scenario 1 — 5 submits, 1 dispatch, 4 replays."""
    await kernel_harness.register_worker(echo_worker_spec)

    first = await kernel_harness.submit(text="echo hello", user_id="alice", event_id=E2)

    replays = [
        await kernel_harness.submit(text="echo hello", user_id="alice", event_id=E2)
        for _ in range(4)
    ]

    assert first.traceOutcome == "all_succeeded"
    for r in replays:
        assert r.traceId == first.traceId
        assert r.eventId == E2
        assert r.traceOutcome == "all_succeeded"

    events = audit_events_factory()
    dispatched_for_trace = [
        e for e in events
        if e.get("eventType") == "task_dispatched" and e.get("traceId") == first.traceId
    ]
    assert len(dispatched_for_trace) == 1, (
        f"expected exactly 1 task_dispatched for {first.traceId}, "
        f"got {len(dispatched_for_trace)}"
    )

    replays_for_event = [
        e for e in events
        if e.get("eventType") == "idempotent_replay"
        and (e.get("extra") or {}).get("eventId") == E2
    ]
    assert len(replays_for_event) == 4, (
        f"expected 4 idempotent_replay events, got {len(replays_for_event)}"
    )
    for e in replays_for_event:
        assert e.get("idempotent_replay") is True


@pytest.mark.integration
async def test_idempotent_concurrent_submit_during_running(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Spec §P2 Scenario 2 — 3 parallel submits collapse to a single trace."""
    await kernel_harness.register_worker(echo_worker_spec)

    results = await asyncio.gather(
        kernel_harness.submit(text="echo hello", user_id="alice", event_id=E3),
        kernel_harness.submit(text="echo hello", user_id="alice", event_id=E3),
        kernel_harness.submit(text="echo hello", user_id="alice", event_id=E3),
    )

    trace_ids = {r.traceId for r in results}
    assert len(trace_ids) == 1, (
        f"concurrent submits must share one traceId; got {trace_ids!r}"
    )
    (only_trace,) = trace_ids

    events = audit_events_factory()
    dispatched_for_trace = [
        e for e in events
        if e.get("eventType") == "task_dispatched" and e.get("traceId") == only_trace
    ]
    assert len(dispatched_for_trace) == 1


@pytest.mark.integration
async def test_idempotent_replay_after_failure(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Spec §P2 Scenario 3 — failed traces replay with identical outcome."""
    await kernel_harness.register_worker(echo_worker_spec)

    first = await kernel_harness.submit(
        text="click 100,200", user_id="alice", event_id=E4
    )
    assert first.traceOutcome == "all_failed"

    replay = await kernel_harness.submit(
        text="click 100,200", user_id="alice", event_id=E4
    )

    assert replay.traceId == first.traceId
    assert replay.traceOutcome == "all_failed"

    events = audit_events_factory()
    failures_for_trace = [
        e for e in events
        if e.get("eventType") == "task_failed" and e.get("traceId") == first.traceId
    ]
    assert len(failures_for_trace) == 1, (
        "replay must NOT re-emit task_failed; original event is the single source of truth"
    )


@pytest.mark.integration
async def test_idempotency_key_is_user_plus_event(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec: WorkerSpec,
) -> None:
    """Edge case — same eventId under different users are independent traces."""
    await kernel_harness.register_worker(echo_worker_spec)

    alice = await kernel_harness.submit(
        text="echo hello", user_id="alice", event_id=E2
    )
    bob = await kernel_harness.submit(
        text="echo hello", user_id="bob", event_id=E2
    )

    assert alice.traceId != bob.traceId
    assert alice.traceOutcome == "all_succeeded"
    assert bob.traceOutcome == "all_succeeded"
