"""Phase N.1 RED — HeartbeatTracker unit contract (FR-009 / T076).

Pins the kernel-side liveness watchdog. The tracker owns no I/O: tests
inject a fake ``now()`` callable + capture callback invocations, so the
"3 consecutive missed heartbeats" rule can be verified deterministically
on any host (no sleeps, no flake).

Contract (closed under this file):

* ``track(worker_id)`` registers a fresh liveness record with the
  current ``now()``.
* ``untrack(worker_id)`` removes it silently; subsequent feeds are ignored.
* ``feed(worker_id)`` bumps ``last_seen`` to ``now()``. If the worker was
  previously marked unhealthy, the tracker MUST fire ``on_recovered``
  exactly once.
* The ``run()`` coroutine walks every tracked worker at roughly
  ``interval_s / 2`` cadence. If ``now() - last_seen > interval_s *
  miss_threshold`` it fires ``on_unhealthy`` exactly once and keeps the
  worker in a "currently unhealthy" set until recovery.
* ``stop()`` makes the ``run()`` coroutine return promptly.

These tests fail RED because
``orchestrator_kernel.worker_supervisor.lifecycle.HeartbeatTracker`` does
not exist yet. Landing the class satisfies every case below.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest


class _FakeClock:
    """Mutable monotonic clock; callers bump .now to advance time."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def _noop(worker_id: str) -> None:
    """Default callback used when a test does not care about this branch."""
    return None


# --- static feed / untrack semantics ---------------------------------------


def test_track_and_feed_do_not_raise() -> None:
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    tracker = HeartbeatTracker(
        interval_s=0.5,
        miss_threshold=3,
        on_unhealthy=_noop,
        on_recovered=_noop,
        now=clock,
    )
    tracker.track("worker-a")
    tracker.feed("worker-a")  # should be idempotent
    tracker.feed("worker-unknown")  # must silently ignore


def test_untrack_discards_last_seen() -> None:
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    tracker = HeartbeatTracker(
        interval_s=0.5,
        miss_threshold=3,
        on_unhealthy=_noop,
        on_recovered=_noop,
        now=clock,
    )
    tracker.track("worker-a")
    tracker.untrack("worker-a")
    tracker.feed("worker-a")  # post-untrack: silently ignored

    # Fast-forward way past the stale threshold; run() should NOT fire
    # on_unhealthy because no worker is tracked any more.
    clock.now += 10.0
    fired: list[str] = []

    async def _flag(wid: str) -> None:
        fired.append(wid)

    tracker._on_unhealthy = _flag  # type: ignore[attr-defined]
    tracker.stop()  # run() would have exited anyway; double-stop is safe
    assert not fired


# --- run() lifecycle: miss -> unhealthy -> recover -------------------------


@pytest.mark.asyncio
async def test_three_missed_intervals_flip_worker_unhealthy() -> None:
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    unhealthy_calls: list[str] = []
    recovered_calls: list[str] = []

    async def _on_unhealthy(wid: str) -> None:
        unhealthy_calls.append(wid)

    async def _on_recovered(wid: str) -> None:
        recovered_calls.append(wid)

    tracker = HeartbeatTracker(
        interval_s=0.05,  # fast ticks so the test completes in < 1 s
        miss_threshold=3,
        on_unhealthy=_on_unhealthy,
        on_recovered=_on_recovered,
        now=clock,
    )

    async def _driver() -> None:
        tracker.track("worker-a")
        # Advance the fake clock past 3 × interval_s while the run() loop
        # polls on the real event loop. Real-time gap (~0.1 s) lets the
        # tracker's internal sleep tick at least once.
        await asyncio.sleep(0.12)
        clock.now += 1.0
        await asyncio.sleep(0.12)
        tracker.stop()

    run_task = asyncio.create_task(tracker.run())
    await _driver()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert unhealthy_calls == ["worker-a"], (
        f"expected exactly one unhealthy flip, got {unhealthy_calls!r}"
    )
    assert recovered_calls == []


@pytest.mark.asyncio
async def test_recover_after_unhealthy() -> None:
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    unhealthy_calls: list[str] = []
    recovered_calls: list[str] = []

    async def _on_unhealthy(wid: str) -> None:
        unhealthy_calls.append(wid)

    async def _on_recovered(wid: str) -> None:
        recovered_calls.append(wid)

    tracker = HeartbeatTracker(
        interval_s=0.05,
        miss_threshold=3,
        on_unhealthy=_on_unhealthy,
        on_recovered=_on_recovered,
        now=clock,
    )

    async def _driver() -> None:
        tracker.track("worker-a")
        await asyncio.sleep(0.08)
        clock.now += 1.0  # trip stale threshold
        await asyncio.sleep(0.12)
        # Heartbeat arrives, bumping last_seen past the stale gap.
        tracker.feed("worker-a")
        await asyncio.sleep(0.05)
        tracker.stop()

    run_task = asyncio.create_task(tracker.run())
    await _driver()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert unhealthy_calls == ["worker-a"]
    assert recovered_calls == ["worker-a"]


@pytest.mark.asyncio
async def test_unhealthy_fires_at_most_once_per_episode() -> None:
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    unhealthy_calls: list[str] = []

    async def _on_unhealthy(wid: str) -> None:
        unhealthy_calls.append(wid)

    tracker = HeartbeatTracker(
        interval_s=0.02,
        miss_threshold=3,
        on_unhealthy=_on_unhealthy,
        on_recovered=_noop,
        now=clock,
    )

    async def _driver() -> None:
        tracker.track("worker-a")
        await asyncio.sleep(0.03)
        clock.now += 5.0  # many multiples of the stale window
        await asyncio.sleep(0.15)
        tracker.stop()

    run_task = asyncio.create_task(tracker.run())
    await _driver()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert unhealthy_calls == ["worker-a"], (
        f"multiple unhealthy fires: {unhealthy_calls!r}"
    )


@pytest.mark.asyncio
async def test_stop_returns_promptly() -> None:
    """Baseline smoke: run() must quit within 100 ms of stop()."""
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    tracker = HeartbeatTracker(
        interval_s=1.0,
        miss_threshold=3,
        on_unhealthy=_noop,
        on_recovered=_noop,
        now=clock,
    )
    run_task = asyncio.create_task(tracker.run())
    await asyncio.sleep(0.02)
    tracker.stop()
    await asyncio.wait_for(run_task, timeout=0.2)


# --- async-vs-sync callback parity ----------------------------------------


@pytest.mark.asyncio
async def test_sync_callbacks_are_also_supported() -> None:
    """Mixing sync + async callbacks is OK; both receive the worker_id."""
    from orchestrator_kernel.worker_supervisor.lifecycle import HeartbeatTracker

    clock = _FakeClock()
    fired: list[tuple[str, str]] = []

    def _sync_unhealthy(wid: str) -> None:
        fired.append(("unhealthy", wid))

    async def _async_recover(wid: str) -> Awaitable[None] | None:  # type: ignore[return-value]
        fired.append(("recovered", wid))
        return None

    tracker = HeartbeatTracker(
        interval_s=0.02,
        miss_threshold=3,
        on_unhealthy=_sync_unhealthy,
        on_recovered=_async_recover,
        now=clock,
    )

    async def _driver() -> None:
        tracker.track("worker-a")
        await asyncio.sleep(0.03)
        clock.now += 1.0
        await asyncio.sleep(0.08)
        tracker.feed("worker-a")
        await asyncio.sleep(0.05)
        tracker.stop()

    run_task = asyncio.create_task(tracker.run())
    await _driver()
    await asyncio.wait_for(run_task, timeout=1.0)

    events = [kind for kind, _ in fired]
    assert events == ["unhealthy", "recovered"], f"got {fired!r}"
