"""T071 — Phase 7 US5 RED: per-worker resource monitor unit tests.

The monitor is the kernel-side sandbox enforcement (FR-025 / SC-005). It
samples a worker process every 500 ms via ``psutil.Process`` and fires a
violation callback the moment the sampled RSS / CPU exceeds the worker's
registered ``ResourceLimits``.

These tests pin the **contract** of two helpers lives in
``orchestrator_kernel.worker_supervisor.lifecycle``:

1. ``check_limits(pid, limits) -> Violation | None`` — pure sampling
   function; returns ``None`` when under budget; a ``Violation`` describing
   the dimension + actual / limit values otherwise.
2. ``watch_worker(pid, limits, interval_s, on_violation) -> None`` —
   async loop; polls ``check_limits`` every ``interval_s``; stops on first
   violation *or* when ``psutil.NoSuchProcess`` is raised (worker already
   exited naturally).

Both symbols are missing in RED; imports will fail cleanly. Adding the
implementation is T073 GREEN. The tests deliberately monkeypatch
``psutil.Process`` so they stay deterministic on Windows / POSIX.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

# --- check_limits contract -------------------------------------------------


def test_check_limits_under_budget_returns_none(monkeypatch):
    from orchestrator_kernel.worker_supervisor import lifecycle

    class _FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=32 * 1024 * 1024)  # 32 MiB

        def cpu_percent(self, interval: float | None = None) -> float:
            return 5.0

    monkeypatch.setattr(lifecycle, "_psutil_process", _FakeProc)

    limits = lifecycle.ResourceLimitsSnapshot(memory_mb=128, cpu_pct=50)
    result = lifecycle.check_limits(pid=1234, limits=limits)
    assert result is None


def test_check_limits_memory_over_budget_returns_violation(monkeypatch):
    from orchestrator_kernel.worker_supervisor import lifecycle

    class _FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=200 * 1024 * 1024)  # 200 MiB

        def cpu_percent(self, interval: float | None = None) -> float:
            return 2.0

    monkeypatch.setattr(lifecycle, "_psutil_process", _FakeProc)

    limits = lifecycle.ResourceLimitsSnapshot(memory_mb=128, cpu_pct=50)
    result = lifecycle.check_limits(pid=1234, limits=limits)
    assert result is not None
    assert result.dimension == "memory_mb"
    assert result.actual > result.limit


def test_check_limits_disappeared_process_returns_none(monkeypatch):
    """If psutil raises NoSuchProcess, the monitor treats it as benign."""
    from orchestrator_kernel.worker_supervisor import lifecycle

    def _raise(pid: int):
        import psutil

        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(lifecycle, "_psutil_process", _raise)

    limits = lifecycle.ResourceLimitsSnapshot(memory_mb=128, cpu_pct=50)
    result = lifecycle.check_limits(pid=9999, limits=limits)
    assert result is None


# --- watch_worker contract -------------------------------------------------


@pytest.mark.asyncio
async def test_watch_worker_polls_at_interval_until_violation(monkeypatch):
    """watch_worker must call check_limits at roughly interval_s cadence."""
    from orchestrator_kernel.worker_supervisor import lifecycle

    sample_times: list[float] = []
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    # First two samples clean, third one fires a violation.
    responses = iter(
        [
            None,
            None,
            lifecycle.Violation(
                dimension="memory_mb", actual=256, limit=128
            ),
        ]
    )

    def _fake_check(*, pid: int, limits) -> object | None:
        sample_times.append(loop.time() - t0)
        return next(responses)

    monkeypatch.setattr(lifecycle, "check_limits", _fake_check)

    fired: list[object] = []

    async def _on_violation(v) -> None:
        fired.append(v)

    limits = lifecycle.ResourceLimitsSnapshot(memory_mb=128, cpu_pct=50)
    await asyncio.wait_for(
        lifecycle.watch_worker(
            pid=42,
            limits=limits,
            interval_s=0.1,
            on_violation=_on_violation,
        ),
        timeout=2.0,
    )

    assert len(fired) == 1
    assert len(sample_times) == 3
    # Cadence at ~0.1 s (allow 50 % wobble for Windows scheduler jitter).
    for earlier, later in zip(sample_times, sample_times[1:], strict=False):
        gap = later - earlier
        assert 0.05 <= gap <= 0.25, (
            f"watch_worker interval drift: got {gap:.3f}s"
        )


@pytest.mark.asyncio
async def test_watch_worker_stops_when_process_exits(monkeypatch):
    """watch_worker returns cleanly if the worker is already gone."""
    from orchestrator_kernel.worker_supervisor import lifecycle

    calls: list[int] = []

    def _fake_check(*, pid: int, limits) -> object | None:
        calls.append(pid)
        # Simulate NoSuchProcess propagation via a None return (already
        # "handled" upstream), then signal by raising on the 2nd call.
        if len(calls) >= 2:
            import psutil

            raise psutil.NoSuchProcess(pid)
        return None

    monkeypatch.setattr(lifecycle, "check_limits", _fake_check)

    async def _on_violation(v) -> None:  # pragma: no cover - must NOT fire
        raise AssertionError("violation fired on exited process")

    limits = lifecycle.ResourceLimitsSnapshot(memory_mb=128, cpu_pct=50)
    await asyncio.wait_for(
        lifecycle.watch_worker(
            pid=42,
            limits=limits,
            interval_s=0.05,
            on_violation=_on_violation,
        ),
        timeout=1.0,
    )
    assert len(calls) >= 1
