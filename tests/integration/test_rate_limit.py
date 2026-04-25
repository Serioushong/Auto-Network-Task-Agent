"""T088 — Phase 9 US7 RED: 4-dimensional rate limiter wired into intake.

FR-026 / FR-027 + spec.md §速率限制与并发控制 require the kernel intake
pipeline to gate every event against four dimensions, in this fixed
priority order:

  1. ``global_rps``               — token bucket, deploy-wide
  2. ``user_rpm``                 — sliding 60 s window per userId
  3. ``user_concurrent``          — inflight per userId
  4. ``user_highrisk_concurrent`` — inflight HIGH_RISK per userId
                                    (covered by ``test_highrisk_flood``)

Each rejection MUST:
  * return ``traceOutcome == "rejected"``;
  * land an ``event_rejected_rate_limited`` audit row whose
    ``extra.dimension`` names which gate tripped;
  * NOT create any Task (no ``trace_created`` / ``task_created`` row).

Each admission MUST decrement the per-user concurrent counter when the
trace terminates so future submits proceed.

This file drives the kernel via the ``rate_limited_harness`` fixture
defined below, which exposes new ``assemble_kernel`` knobs (RED until
T092):

  * ``rate_limits=RateLimits(...)``
  * ``rate_limiter_clock=Callable[[], datetime]``
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from orchestrator_kernel.kernel.rate_limit import RateLimits

from ._harness import WorkerSpec

# --- harness factories ------------------------------------------------------


class _Clock:
    """Mutable clock the test can advance manually — frozen by default."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _build_assembler() -> Any:
    from orchestrator_kernel.cli_main import assemble_kernel  # type: ignore[attr-defined]

    return assemble_kernel


@pytest_asyncio.fixture
async def rate_limited_harness(
    tmp_audit_dir: Path,
) -> AsyncIterator[tuple[Any, _Clock]]:
    """Default-limits harness with frozen clock, for global_rps tests."""
    assemble = _build_assembler()
    clock = _Clock()
    harness = await assemble(
        audit_dir=tmp_audit_dir,
        rate_limits=RateLimits(
            global_rps=2, user_rpm=10, user_concurrent=10
        ),
        rate_limiter_clock=clock,
    )
    try:
        yield harness, clock
    finally:
        await harness.shutdown()


@pytest_asyncio.fixture
async def rpm_harness(
    tmp_audit_dir: Path,
) -> AsyncIterator[tuple[Any, _Clock]]:
    """Tiny user_rpm so we can exhaust the sliding window in 4 calls."""
    assemble = _build_assembler()
    clock = _Clock()
    harness = await assemble(
        audit_dir=tmp_audit_dir,
        rate_limits=RateLimits(
            global_rps=50, user_rpm=3, user_concurrent=10
        ),
        rate_limiter_clock=clock,
    )
    try:
        yield harness, clock
    finally:
        await harness.shutdown()


@pytest_asyncio.fixture
async def concurrent_harness(
    tmp_audit_dir: Path,
) -> AsyncIterator[Any]:
    """Tiny user_concurrent so 2 in-flight sleeps reject the 3rd attempt."""
    assemble = _build_assembler()
    harness = await assemble(
        audit_dir=tmp_audit_dir,
        rate_limits=RateLimits(
            global_rps=50, user_rpm=120, user_concurrent=2
        ),
    )
    try:
        yield harness
    finally:
        await harness.shutdown()


@pytest.fixture
def sleep_worker_spec() -> WorkerSpec:
    return WorkerSpec(
        script_path=(
            Path(__file__).resolve().parents[2]
            / "src"
            / "workers_stub"
            / "sleep_worker.py"
        ),
        expected_capabilities=("sleep.wait",),
    )


# --- D1 global_rps ----------------------------------------------------------


@pytest.mark.integration
async def test_global_rps_third_event_rejected(
    rate_limited_harness: tuple[Any, _Clock],
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """global_rps=2, frozen clock, third sequential event MUST reject."""
    harness, _clock = rate_limited_harness
    await harness.register_worker(echo_worker_spec)

    r1 = await harness.submit(text="echo a", user_id="alice", timeout_s=2.0)
    r2 = await harness.submit(text="echo b", user_id="alice", timeout_s=2.0)
    r3 = await harness.submit(text="echo c", user_id="alice", timeout_s=2.0)

    assert r1.traceOutcome == "all_succeeded"
    assert r2.traceOutcome == "all_succeeded"
    assert r3.traceOutcome == "rejected"
    assert "rate" in r3.message.lower()

    rejections = [
        e
        for e in audit_events_factory()
        if e.get("eventType") == "event_rejected_rate_limited"
    ]
    assert len(rejections) == 1
    extra = rejections[0].get("extra") or {}
    assert extra.get("dimension") == "global_rps", extra


@pytest.mark.integration
async def test_global_rps_refills_after_one_second(
    rate_limited_harness: tuple[Any, _Clock],
    echo_worker_spec: WorkerSpec,
) -> None:
    """After clock advances 1 s the bucket refills global_rps tokens."""
    harness, clock = rate_limited_harness
    await harness.register_worker(echo_worker_spec)

    await harness.submit(text="echo 1", user_id="alice", timeout_s=2.0)
    await harness.submit(text="echo 2", user_id="alice", timeout_s=2.0)
    rejected = await harness.submit(
        text="echo 3", user_id="alice", timeout_s=2.0
    )
    assert rejected.traceOutcome == "rejected"

    clock.advance(1.0)  # full refill
    again = await harness.submit(
        text="echo 4", user_id="alice", timeout_s=2.0
    )
    assert again.traceOutcome == "all_succeeded"


# --- D2 user_rpm ------------------------------------------------------------


@pytest.mark.integration
async def test_user_rpm_fourth_event_rejected(
    rpm_harness: tuple[Any, _Clock],
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """user_rpm=3, frozen clock; the fourth event in 60 s rejects."""
    harness, _clock = rpm_harness
    await harness.register_worker(echo_worker_spec)

    for i in range(3):
        r = await harness.submit(
            text=f"echo r{i}", user_id="bob", timeout_s=2.0
        )
        assert r.traceOutcome == "all_succeeded", (i, r.traceOutcome)

    fourth = await harness.submit(
        text="echo r3", user_id="bob", timeout_s=2.0
    )
    assert fourth.traceOutcome == "rejected"

    rejections = [
        e
        for e in audit_events_factory()
        if e.get("eventType") == "event_rejected_rate_limited"
    ]
    assert len(rejections) == 1
    assert (rejections[0].get("extra") or {}).get("dimension") == "user_rpm"


@pytest.mark.integration
async def test_user_rpm_window_slides(
    rpm_harness: tuple[Any, _Clock],
    echo_worker_spec: WorkerSpec,
) -> None:
    """61 s after the first event, the window slides and a slot frees up."""
    harness, clock = rpm_harness
    await harness.register_worker(echo_worker_spec)

    for i in range(3):
        await harness.submit(text=f"echo s{i}", user_id="bob", timeout_s=2.0)
    rejected = await harness.submit(
        text="echo s3", user_id="bob", timeout_s=2.0
    )
    assert rejected.traceOutcome == "rejected"

    clock.advance(61.0)
    again = await harness.submit(
        text="echo s4", user_id="bob", timeout_s=2.0
    )
    assert again.traceOutcome == "all_succeeded"


# --- D3 user_concurrent -----------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform.startswith("win") and sys.version_info < (3, 11),
    reason="async subprocess on Win + py<3.11 occasionally hangs",
)
async def test_user_concurrent_third_inflight_rejected(
    concurrent_harness: Any,
    sleep_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """user_concurrent=2, three concurrent sleeps → third rejects."""
    harness = concurrent_harness
    await harness.register_worker(sleep_worker_spec)

    # Two slow sleeps held in flight; one must run, the next races.
    slow_a = asyncio.create_task(
        harness.submit(text="sleep 0.6", user_id="carol", timeout_s=4.0)
    )
    slow_b = asyncio.create_task(
        harness.submit(text="sleep 0.6", user_id="carol", timeout_s=4.0)
    )
    await asyncio.sleep(0.15)  # let both clear admission

    rejected = await harness.submit(
        text="sleep 0.6", user_id="carol", timeout_s=4.0
    )
    assert rejected.traceOutcome == "rejected"
    assert "rate" in rejected.message.lower()

    res_a, res_b = await asyncio.gather(slow_a, slow_b)
    assert res_a.traceOutcome == "all_succeeded"
    assert res_b.traceOutcome == "all_succeeded"

    rejections = [
        e
        for e in audit_events_factory()
        if e.get("eventType") == "event_rejected_rate_limited"
    ]
    assert len(rejections) == 1
    assert (
        (rejections[0].get("extra") or {}).get("dimension")
        == "user_concurrent"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform.startswith("win") and sys.version_info < (3, 11),
    reason="async subprocess on Win + py<3.11 occasionally hangs",
)
async def test_user_concurrent_releases_after_terminal(
    concurrent_harness: Any,
    sleep_worker_spec: WorkerSpec,
) -> None:
    """After both in-flight sleeps end, a fresh submit MUST be admitted."""
    harness = concurrent_harness
    await harness.register_worker(sleep_worker_spec)

    a = await harness.submit(text="sleep 0.05", user_id="dave", timeout_s=2.0)
    b = await harness.submit(text="sleep 0.05", user_id="dave", timeout_s=2.0)
    assert a.traceOutcome == "all_succeeded"
    assert b.traceOutcome == "all_succeeded"

    c = await harness.submit(text="sleep 0.05", user_id="dave", timeout_s=2.0)
    assert c.traceOutcome == "all_succeeded"


# --- ordering: global_rps wins when multiple gates would trip ---------------


@pytest.mark.integration
async def test_dimension_priority_global_before_user(
    tmp_audit_dir: Path,
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """If both global_rps AND user_rpm would trip, global_rps MUST be reported.

    Spec ordering (FR-026 / FR-027): the four dimensions are checked in
    a fixed priority. With limits ``global_rps=1`` + ``user_rpm=1`` the
    first event admits both gates; the second event would trip both, and
    the audit MUST surface the highest-priority dimension only.
    """
    assemble = _build_assembler()
    clock = _Clock()
    harness = await assemble(
        audit_dir=tmp_audit_dir,
        rate_limits=RateLimits(global_rps=1, user_rpm=1, user_concurrent=10),
        rate_limiter_clock=clock,
    )
    try:
        await harness.register_worker(echo_worker_spec)
        first = await harness.submit(
            text="echo p1", user_id="erin", timeout_s=2.0
        )
        assert first.traceOutcome == "all_succeeded"

        second = await harness.submit(
            text="echo p2", user_id="erin", timeout_s=2.0
        )
        assert second.traceOutcome == "rejected"

        rejections = [
            e
            for e in audit_events_factory()
            if e.get("eventType") == "event_rejected_rate_limited"
        ]
        assert len(rejections) == 1
        # FR-027 + spec doc list global_rps as dim 1 → it wins.
        assert (
            (rejections[0].get("extra") or {}).get("dimension")
            == "global_rps"
        )
    finally:
        await harness.shutdown()


# --- queue invariant: rejected events MUST NOT create tasks ----------------


@pytest.mark.integration
async def test_rejected_event_creates_no_task(
    rate_limited_harness: tuple[Any, _Clock],
    echo_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Rate-limit rejection must short-circuit before trace_created."""
    harness, _clock = rate_limited_harness
    await harness.register_worker(echo_worker_spec)

    await harness.submit(text="echo q1", user_id="frank", timeout_s=2.0)
    await harness.submit(text="echo q2", user_id="frank", timeout_s=2.0)
    third = await harness.submit(
        text="echo q3", user_id="frank", timeout_s=2.0
    )
    assert third.traceOutcome == "rejected"

    events = audit_events_factory()
    trace_creates = [
        e for e in events if e.get("eventType") == "trace_created"
    ]
    task_creates = [
        e for e in events if e.get("eventType") == "task_created"
    ]
    # Two admitted submits → two trace_created. task_created fires per
    # node in the task tree (root + leaf for an echo trace), so the count
    # MUST be a multiple of trace_creates and the rejected submit MUST
    # contribute zero rows.
    assert len(trace_creates) == 2, (
        f"expected exactly 2 trace_created (the admitted pair), "
        f"got {len(trace_creates)}"
    )
    nodes_per_admitted = len(task_creates) // len(trace_creates)
    assert nodes_per_admitted * len(trace_creates) == len(task_creates), (
        f"task_created not evenly distributed: trace_created="
        f"{len(trace_creates)}, task_created={len(task_creates)}"
    )
    # The rejected submit MUST NOT produce its own root/leaf rows: the
    # invariant is that no extra trace_id appears among the task_created
    # rows beyond the two admitted ones.
    trace_ids_seen = {
        e.get("traceId") for e in task_creates if e.get("traceId")
    }
    assert len(trace_ids_seen) == 2, (
        f"task_created leaked from the rejected submit: "
        f"trace_ids={sorted(trace_ids_seen)}"
    )
