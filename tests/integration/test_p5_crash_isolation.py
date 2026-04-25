"""T070 — Phase 7 US5 RED: worker crash isolation.

Covers spec.md §P5 Acceptance Scenario 1 + SC-005 + INV-4:

1. **Scenario 1 — worker raises exception in the middle of 3 parallel
   traces**: 3 traces submitted concurrently (2 echo.say, 1 crash.raise);
   the crash worker dies mid-dispatch; the other two traces **still
   complete ``succeeded``** and the kernel PID is unchanged end-to-end
   (SC-005: main-loop resilience).

2. **INV-4 — no orphan runtime tasks**: after ``submit()`` returns for
   all three traces, the ``CancelManager`` is holding zero live sessions;
   every ``task_dispatched`` in the audit chain has a matching terminal
   event (``task_succeeded`` or ``task_failed``) whose ``extra.failureReason``
   is exactly ``worker_crashed`` for the crash trace and nothing for the
   two echo traces.

The RED mode fails because:
* ``crash_worker.py`` does not exist yet (T075) → ``WorkerSpawnError``;
* the harness plan router returns ``desktop.click`` for "crash" text, so
  dispatch would route to the wrong worker (T077 extends ``_plan_capability``).

Both gaps close once T075 + T077 land.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.fixture
def crash_worker_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "crash_worker.py"
    )


@pytest.fixture
def crash_worker_spec(crash_worker_script: Path) -> WorkerSpec:
    return WorkerSpec(
        script_path=crash_worker_script,
        expected_capabilities=("crash.raise", "crash.oom"),
    )


@pytest.fixture
def echo_worker_script_p5() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "echo_worker.py"
    )


@pytest.fixture
def echo_worker_spec_p5(echo_worker_script_p5: Path) -> WorkerSpec:
    return WorkerSpec(
        script_path=echo_worker_script_p5,
        expected_capabilities=("echo.say",),
    )


@pytest.mark.integration
async def test_crash_does_not_poison_peer_traces(
    kernel_harness: KernelHarnessProtocol,
    echo_worker_spec_p5: WorkerSpec,
    crash_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """3 parallel traces; 1 crashes; others finish clean; kernel PID stable."""
    kernel_pid_before = os.getpid()

    await kernel_harness.register_worker(echo_worker_spec_p5)
    await kernel_harness.register_worker(crash_worker_spec)

    # Submit 3 traces concurrently: 2 echo (will succeed), 1 crash (will fail).
    tasks = [
        asyncio.create_task(
            kernel_harness.submit(
                text="echo alpha", user_id="alice", timeout_s=10.0
            )
        ),
        asyncio.create_task(
            kernel_harness.submit(
                text="crash raise", user_id="bob", timeout_s=10.0
            )
        ),
        asyncio.create_task(
            kernel_harness.submit(
                text="echo gamma", user_id="carol", timeout_s=10.0
            )
        ),
    ]
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15.0)

    kernel_pid_after = os.getpid()
    assert kernel_pid_before == kernel_pid_after, (
        "SC-005 violated: kernel PID changed during peer-worker crash"
    )

    outcomes = [r.traceOutcome for r in results]
    succeeded = sum(1 for o in outcomes if o in {"succeeded", "all_succeeded"})
    failed = sum(1 for o in outcomes if o in {"failed", "all_failed"})
    assert succeeded == 2, (
        f"expected 2 succeeded peer traces, got outcomes={outcomes!r}"
    )
    assert failed == 1, (
        f"expected 1 failed (crash) trace, got outcomes={outcomes!r}"
    )

    # Audit chain: every task_dispatched has a matching terminal, and exactly
    # one task_failed carries failureReason=worker_crashed.
    events = audit_events_factory()
    dispatched = {
        e["taskId"]
        for e in events
        if e.get("eventType") == "task_dispatched"
    }
    terminal = {
        e["taskId"]
        for e in events
        if e.get("eventType") in {"task_succeeded", "task_failed"}
    }
    assert dispatched == terminal, (
        f"INV-4 violated: unmatched task ids "
        f"dispatched_only={dispatched - terminal}, "
        f"terminal_only={terminal - dispatched}"
    )

    crash_failures = [
        e for e in events
        if e.get("eventType") == "task_failed"
        and (e.get("extra") or {}).get("failureReason") == "worker_crashed"
    ]
    assert len(crash_failures) == 1, (
        f"expected exactly 1 worker_crashed, got {len(crash_failures)}"
    )

    # INV-4 part 2: no live CancelManager sessions remain.
    # Accessed via harness internals; acceptable for integration test.
    live_sessions = getattr(
        getattr(kernel_harness, "_cancel_manager", None),
        "_sessions",
        {},
    )
    assert not live_sessions, (
        f"INV-4 violated: leaked cancel sessions after submits: "
        f"{list(live_sessions)!r}"
    )
