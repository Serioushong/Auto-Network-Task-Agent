"""Phase N.2 RED — Harness sandbox-violation wiring (FR-025 / T074 / T076 tie-in).

Pins the kernel-side contract that when the per-worker resource monitor
reports a ``Violation``:

1. the harness writes a ``sandbox_limit_hit`` audit event (with
   ``workerId``, ``dimension``, ``actual``, ``limit``);
2. the offending worker is torn down via
   ``_tear_down_tainted_worker`` so subsequent dispatches do not reuse
   its (possibly about-to-die) stdio;
3. any task that was mid-dispatch on that worker observes the
   stdout-EOF via the reader and fails the leaf with
   ``failureReason="sandbox_limit"`` (not ``worker_crashed``) because
   the channel sets a short-lived "sandboxed" flag before the kill.

The test injects a *fake* ``resource_monitor_factory`` that fires one
``Violation`` callback immediately after register, so the test never
depends on real psutil sampling timing. The factory contract is the
only new surface on ``KernelHarness``; the rest of the wiring reuses
Round-1's reader / channel plumbing.

RED fails because:
* ``KernelHarness.__init__`` does not accept
  ``resource_monitor_factory`` yet;
* ``_on_sandbox_violation`` / the ``sandbox_limit_hit`` audit path
  does not exist yet;
* ``_execute_leaf`` does not map sandboxed-channel failures to
  ``failureReason="sandbox_limit"``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from orchestrator_kernel.audit.writer import AuditWriter
from orchestrator_kernel.kernel.dispatcher import Dispatcher
from orchestrator_kernel.worker_supervisor.lifecycle import Violation
from orchestrator_kernel.worker_supervisor.supervisor import WorkerSupervisor


@dataclass
class _WorkerSpec:
    script_path: Path
    expected_capabilities: tuple[str, ...] = ()
    resource_limits_override: Any = None


@pytest.fixture
def silent_worker_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "silent_worker.py"
    )


@pytest.fixture
def sleep_worker_script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "sleep_worker.py"
    )


def _read_audit_events(audit_dir: Path) -> list[dict[str, Any]]:
    import json

    files = sorted(audit_dir.glob("audit-*.jsonl"))
    events: list[dict[str, Any]] = []
    for fp in files:
        for line in fp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


async def test_violation_emits_sandbox_audit_and_tears_down_worker(
    tmp_path, silent_worker_script: Path
) -> None:
    """One on_violation call → audit + worker untracked/killed."""
    from orchestrator_kernel.cli_main import KernelHarness

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = AuditWriter(audit_dir)

    violation_fired: asyncio.Event = asyncio.Event()
    captured_callbacks: list[Any] = []

    def _fake_monitor_factory(
        worker: Any, limits: Any, on_violation: Any
    ) -> asyncio.Task[None]:
        captured_callbacks.append(on_violation)

        async def _runner() -> None:
            await asyncio.sleep(0.05)
            await on_violation(
                Violation(dimension="memory_mb", actual=128.0, limit=64.0)
            )
            violation_fired.set()
            # Task ends here; the harness must treat the monitor returning
            # as a normal lifecycle event, not an error.

        return asyncio.create_task(_runner(), name=f"fake-monitor-{worker.worker_id}")

    harness = KernelHarness(
        audit_writer=audit,
        dispatcher=Dispatcher(),
        supervisor=WorkerSupervisor(),
        resource_monitor_factory=_fake_monitor_factory,
    )
    await harness.register_worker(
        _WorkerSpec(
            script_path=silent_worker_script,
            expected_capabilities=("silent.noop",),
        )
    )
    try:
        await asyncio.wait_for(violation_fired.wait(), timeout=2.0)
        # Give the harness one tick to observe the violation + emit audit.
        await asyncio.sleep(0.2)

        events = _read_audit_events(audit_dir)
        sandbox_events = [e for e in events if e.get("eventType") == "sandbox_limit_hit"]
        assert sandbox_events, (
            f"expected sandbox_limit_hit audit, got "
            f"{sorted({e.get('eventType') for e in events})!r}"
        )
        extra = sandbox_events[0].get("extra") or {}
        assert extra.get("dimension") == "memory_mb"
        assert extra.get("actual") == 128.0
        assert extra.get("limit") == 64.0

        # After the violation the worker must be gone from the dispatcher.
        assert harness._channels == {}, (
            f"expected channel removed after sandbox kill, got {list(harness._channels)!r}"
        )
    finally:
        await harness.shutdown()


async def test_dispatch_during_violation_maps_to_sandbox_limit(
    tmp_path, sleep_worker_script: Path
) -> None:
    """A leaf executing on a sandboxed worker fails with sandbox_limit, not worker_crashed."""
    from orchestrator_kernel.cli_main import KernelHarness

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    audit = AuditWriter(audit_dir)

    def _fake_monitor_factory(
        worker: Any, limits: Any, on_violation: Any
    ) -> asyncio.Task[None]:
        async def _runner() -> None:
            # Fire the violation ~300 ms after register so the sleep
            # worker is firmly inside its 3 s nap when the kernel tears
            # it down. The harness must flag the channel "sandboxed"
            # before the teardown causes stdout EOF.
            await asyncio.sleep(0.3)
            await on_violation(
                Violation(dimension="memory", actual=999.0, limit=64.0)
            )

        return asyncio.create_task(_runner(), name=f"fake-monitor-{worker.worker_id}")

    harness = KernelHarness(
        audit_writer=audit,
        dispatcher=Dispatcher(),
        supervisor=WorkerSupervisor(),
        resource_monitor_factory=_fake_monitor_factory,
    )
    await harness.register_worker(
        _WorkerSpec(
            script_path=sleep_worker_script,
            expected_capabilities=("sleep.wait",),
        )
    )

    try:
        result = await harness.submit(
            text="sleep 3", user_id="alice", timeout_s=5.0
        )
        events = _read_audit_events(audit_dir)
        failures = [e for e in events if e.get("eventType") == "task_failed"]
        reasons = [(e.get("extra") or {}).get("failureReason") for e in failures]
        assert "sandbox_limit" in reasons, (
            f"expected sandbox_limit failure reason; got {reasons!r} "
            f"eventTypes={sorted({e.get('eventType') for e in events})!r} "
            f"traceOutcome={result.traceOutcome!r}"
        )
    finally:
        await harness.shutdown()
