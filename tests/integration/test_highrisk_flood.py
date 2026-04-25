"""T090 — Phase 9 US7 RED: HIGH_RISK approval-flood guard (FR-025 D4).

Spec.md §速率限制与并发控制 / FR-025 second clause:
    HIGH_RISK：同一用户"未决 / 运行中 HIGH_RISK Task"数量 MUST ≤ 1
    （新 HIGH_RISK 事件 MUST 在既有 HIGH_RISK Task 结束前被拒绝）。

Edge case (spec.md §Edge Cases — HIGH_RISK 审批洪水尝试):
    内核 MUST 按 FR-025 第二条拒绝新事件并回
    rejected(reason=rate_limited, dimension=user_highrisk_concurrent)，
    不得排队、不得触发第二次审批消息，避免审批通道被刷屏。

Test choreography:
1. user_concurrent stays high (10) so D3 never trips.
2. user_highrisk_concurrent = 1 (the spec hard-cap).
3. Submit one ``delete fake.txt`` (HIGH_RISK file.delete leaf) → it
   parks in ``pending_approval`` indefinitely; the planner upgrades the
   leaf risk to HIGH_RISK and the limiter increments the highrisk gate.
4. While the first submit is still pending, fire a second
   ``delete other.txt`` from the same user. It MUST be rejected with
   ``dimension="user_highrisk_concurrent"``, MUST NOT emit a second
   ``approval_request`` audit row, and MUST NOT create a Task tree.
5. After the first submit drains (we approve it), a fresh HIGH_RISK
   submit from the same user MUST succeed — the highrisk counter MUST
   release on terminal.
6. NORMAL traffic from the same user is unaffected by the highrisk
   gate (only the dedicated counter is consumed).

Currently RED — the dedicated highrisk gate (T093) lives in the limiter
but is not yet driven by the kernel intake pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from orchestrator_kernel.kernel.rate_limit import RateLimits

from ._harness import WorkerSpec


@pytest.fixture
def danger_worker_spec() -> WorkerSpec:
    return WorkerSpec(
        script_path=(
            Path(__file__).resolve().parents[2]
            / "src"
            / "workers_stub"
            / "danger_worker.py"
        ),
        expected_capabilities=("file.delete",),
    )


@pytest_asyncio.fixture
async def highrisk_harness(
    tmp_audit_dir: Path,
) -> AsyncIterator[Any]:
    """Moderate approval window (so the first HIGH_RISK parks long enough
    for the flood to hit) + tight highrisk concurrency = 1, leaving
    D1/D2/D3 generous so no other gate accidentally trips during the flood."""
    from orchestrator_kernel.cli_main import assemble_kernel  # type: ignore[attr-defined]

    harness = await assemble_kernel(
        audit_dir=tmp_audit_dir,
        approval_timeout_ms=2_000,
        rate_limits=RateLimits(
            global_rps=50,
            user_rpm=120,
            user_concurrent=10,
            user_highrisk_concurrent=1,
        ),
    )
    try:
        yield harness
    finally:
        await harness.shutdown()


@pytest_asyncio.fixture
async def fast_highrisk_harness(
    tmp_audit_dir: Path,
) -> AsyncIterator[Any]:
    """Same gate but with a 500 ms approval window so the
    'releases on terminal' test runs quickly without external approval."""
    from orchestrator_kernel.cli_main import assemble_kernel  # type: ignore[attr-defined]

    harness = await assemble_kernel(
        audit_dir=tmp_audit_dir,
        approval_timeout_ms=500,
        rate_limits=RateLimits(
            global_rps=50,
            user_rpm=120,
            user_concurrent=10,
            user_highrisk_concurrent=1,
        ),
    )
    try:
        yield harness
    finally:
        await harness.shutdown()


@pytest.mark.integration
async def test_second_highrisk_rejected_while_first_pending(
    highrisk_harness: Any,
    danger_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Second HIGH_RISK from same user during pending_approval MUST reject."""
    harness = highrisk_harness
    await harness.register_worker(danger_worker_spec)

    first = asyncio.create_task(
        harness.submit(text="delete first.txt", user_id="alice", timeout_s=10.0)
    )
    try:
        # Let the first submit reach pending_approval.
        await asyncio.sleep(0.2)
        events = audit_events_factory()
        approval_requests = [
            e for e in events if e.get("eventType") == "task_pending_approval"
        ]
        assert len(approval_requests) == 1, (
            f"first HIGH_RISK MUST emit exactly one task_pending_approval, "
            f"got {len(approval_requests)}"
        )

        # Fire the flood event from the SAME user. It MUST short-circuit
        # near-instantly (well under the approval window), so we wrap the
        # call in a tight wait_for to fail fast if the gate is missing.
        rejected = await asyncio.wait_for(
            harness.submit(
                text="delete second.txt",
                user_id="alice",
                timeout_s=2.0,
            ),
            timeout=0.5,
        )
        assert rejected.traceOutcome == "rejected", rejected.traceOutcome
        assert "rate" in rejected.message.lower()

        events_after = audit_events_factory()
        # No second pending_approval row.
        approval_requests_after = [
            e
            for e in events_after
            if e.get("eventType") == "task_pending_approval"
        ]
        assert len(approval_requests_after) == 1, (
            f"flood event MUST NOT trigger a second task_pending_approval; "
            f"got {len(approval_requests_after)}"
        )
        # Rate-limit audit row with the right dimension.
        rate_limited = [
            e
            for e in events_after
            if e.get("eventType") == "event_rejected_rate_limited"
        ]
        assert len(rate_limited) == 1, rate_limited
        extra = rate_limited[0].get("extra") or {}
        assert extra.get("dimension") == "user_highrisk_concurrent", extra
        assert extra.get("riskLevel") == "HIGH_RISK", extra
        # No new trace_created from the rejected submit.
        trace_creates_after = [
            e for e in events_after if e.get("eventType") == "trace_created"
        ]
        assert len(trace_creates_after) == 1, len(trace_creates_after)
    finally:
        # First submit will time out approval after 2 s; just await it.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(first, timeout=5.0)


@pytest.mark.integration
async def test_highrisk_counter_releases_after_terminal(
    fast_highrisk_harness: Any,
    danger_worker_spec: WorkerSpec,
) -> None:
    """After the first HIGH_RISK trace ends (timeout), the next is admitted.

    We let approval time out (500 ms window) instead of approving so the
    test does not require a worker actually capable of file.delete; the
    invariant we care about is **release-on-terminal** for the highrisk
    counter, which fires for any terminal outcome (succeeded / denied /
    denied_by_timeout / failed).
    """
    harness = fast_highrisk_harness
    await harness.register_worker(danger_worker_spec)

    res1 = await asyncio.wait_for(
        harness.submit(text="delete first.txt", user_id="bob", timeout_s=10.0),
        timeout=5.0,
    )
    assert res1.traceOutcome in {"denied", "all_failed"}, res1.traceOutcome

    # Highrisk counter MUST be back to 0 — second submit gets through the
    # gate and reaches its own approval-timeout terminal in ~500 ms.
    res2 = await asyncio.wait_for(
        harness.submit(text="delete second.txt", user_id="bob", timeout_s=10.0),
        timeout=5.0,
    )
    assert res2.traceOutcome in {"denied", "all_failed"}, res2.traceOutcome
    # Confirm the second submit ran the full approval cycle (not rejected).
    assert "rate" not in res2.message.lower()


@pytest.mark.integration
async def test_normal_traffic_unaffected_by_highrisk_gate(
    highrisk_harness: Any,
    danger_worker_spec: WorkerSpec,
    echo_worker_spec: WorkerSpec,
) -> None:
    """While a HIGH_RISK pends approval, NORMAL submits MUST still admit."""
    harness = highrisk_harness
    await harness.register_worker(danger_worker_spec)
    await harness.register_worker(echo_worker_spec)

    first = asyncio.create_task(
        harness.submit(text="delete pending.txt", user_id="carol", timeout_s=10.0)
    )
    try:
        await asyncio.sleep(0.2)
        # NORMAL echo MUST sail through (D4 only consumes the highrisk slot).
        normal = await asyncio.wait_for(
            harness.submit(text="echo hi", user_id="carol", timeout_s=2.0),
            timeout=2.0,
        )
        assert normal.traceOutcome == "all_succeeded"
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(first, timeout=5.0)


