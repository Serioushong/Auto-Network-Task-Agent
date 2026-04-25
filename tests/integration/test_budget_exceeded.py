"""T072 — Phase 7 US5 RED: wall-clock budget enforcement.

spec.md §P5 says the kernel MUST close out any Task whose dispatch exceeds
its capability ``budget.wall_clock_ms`` with ``task_failed(budget_exceeded,
dim=wall)`` — even if the worker is still alive and unresponsive. FR-013
caps the user-visible latency on this path at 5 s (soft-abort + escalation
window).

The test drives this via a purpose-built ``budget_worker.py`` stub whose
single capability ``budget.burn`` registers with ``wall_clock_ms=300`` but
on dispatch sleeps **much** longer (10 s) while politely ignoring abort
frames. The kernel is expected to:

1. time the dispatch out at ~300 ms (effective timeout = capability
   ``wall_clock_ms``);
2. transition the Task to ``failed`` with
   ``failureReason="budget_exceeded"`` + ``failureDim="wall"``;
3. return control to ``submit()`` within 5 s total.

RED mode fails because:
* ``src/workers_stub/budget_worker.py`` does not exist yet;
* today's ``_execute_leaf`` uses ``submit.timeout_s`` (default 5 s)
  regardless of capability budget — so on a 10 s-sleep worker the
  ``TimeoutError`` maps to ``worker_crashed``, not ``budget_exceeded``.

T077 GREEN wires ``effective_timeout = min(timeout_s, wall_ms/1000)`` and
splits the TimeoutError branch into a budget_exceeded path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.fixture
def budget_worker_spec() -> WorkerSpec:
    return WorkerSpec(
        script_path=(
            Path(__file__).resolve().parents[2]
            / "src"
            / "workers_stub"
            / "budget_worker.py"
        ),
        expected_capabilities=("budget.burn",),
    )


@pytest.mark.integration
async def test_wall_clock_budget_exceeded_within_5s(
    kernel_harness: KernelHarnessProtocol,
    budget_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """Long-running stubborn worker → failed(budget_exceeded, wall) in ≤ 5 s."""
    await kernel_harness.register_worker(budget_worker_spec)

    started = time.monotonic()
    # submit timeout is 10s to prove the *capability* budget (300 ms) wins,
    # not the caller's outer timeout.
    result = await kernel_harness.submit(
        text="burn cpu", user_id="alice", timeout_s=10.0
    )
    elapsed = time.monotonic() - started

    assert elapsed <= 5.0, (
        f"FR-013 violated: budget enforcement took {elapsed:.2f}s > 5s"
    )
    assert result.traceOutcome in {"failed", "all_failed"}
    assert result.leafOutcomes == ["failed"]

    events = audit_events_factory()
    failure = next(
        (e for e in events if e.get("eventType") == "task_failed"),
        None,
    )
    assert failure is not None, "expected a task_failed event"
    extra = failure.get("extra") or {}
    assert extra.get("failureReason") == "budget_exceeded", (
        f"expected failureReason=budget_exceeded, got extra={extra!r}"
    )
    assert extra.get("failureDim") == "wall", (
        f"expected failureDim=wall, got extra={extra!r}"
    )
