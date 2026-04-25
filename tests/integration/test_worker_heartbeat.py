"""Phase N.1 RED — Worker heartbeat end-to-end (FR-009 / T076).

Pins the kernel-side contract that:

1. a Worker that **never emits a heartbeat** after registration gets flipped
   to ``healthy=False`` within ~3× the heartbeat interval and an
   ``worker_unhealthy`` audit event is written;
2. a flipped Worker is immediately excluded from dispatch: a subsequent
   ``submit`` for its only capability fails with
   ``no_capable_worker``.

The test is deliberately **scope-bounded** to the unhealthy direction
because recovery requires a second live heartbeat, which the ``silent``
stub deliberately refuses. Recovery is covered end-to-end by the unit
tests in ``test_heartbeat_tracker.py`` (fake clock) plus a targeted
reader-level round-trip added with the full wiring.

RED fails because:
* ``src/workers_stub/silent_worker.py`` does not exist yet;
* the harness currently does not start any background reader / tracker
  task after ``register_worker``, so the audit event is never emitted.

Both gaps close with the Round-1 GREEN landing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest.fixture
def silent_worker_spec() -> WorkerSpec:
    return WorkerSpec(
        script_path=(
            Path(__file__).resolve().parents[2]
            / "src"
            / "workers_stub"
            / "silent_worker.py"
        ),
        expected_capabilities=("silent.noop",),
    )


@pytest.mark.integration
async def test_silent_worker_flips_unhealthy_and_blocks_dispatch(
    kernel_harness: KernelHarnessProtocol,
    silent_worker_spec: WorkerSpec,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """silent worker never heartbeats → unhealthy → dispatch denied."""
    await kernel_harness.register_worker(silent_worker_spec)

    # Heartbeat interval default = 500 ms, miss_threshold = 3 → stale
    # after ~1.5 s. Give it a forgiving margin.
    await asyncio.sleep(2.5)

    events = audit_events_factory()
    unhealthy = [
        e for e in events if e.get("eventType") == "worker_unhealthy"
    ]
    assert unhealthy, (
        f"expected worker_unhealthy audit within 2.5 s of register, "
        f"got eventTypes={sorted({e.get('eventType') for e in events})!r}"
    )

    # Dispatcher must now refuse to route silent.noop → no_capable_worker.
    result = await kernel_harness.submit(
        text="noop", user_id="alice", timeout_s=3.0
    )
    assert result.traceOutcome in {"failed", "all_failed"}, (
        f"silent.noop dispatch should fail after unhealthy flip; "
        f"got traceOutcome={result.traceOutcome!r}"
    )
    failures = [
        e for e in audit_events_factory()
        if e.get("eventType") == "task_failed"
    ]
    assert any(
        (f.get("extra") or {}).get("failureReason") == "no_capable_worker"
        for f in failures
    ), (
        f"expected at least one task_failed(no_capable_worker); "
        f"got failures={[(f.get('extra') or {}).get('failureReason') for f in failures]!r}"
    )
