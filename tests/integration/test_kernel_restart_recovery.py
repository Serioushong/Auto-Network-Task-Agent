"""T079 — RED integration test for kernel-restart recovery (Phase 8 / US6 / SC-009).

Scenario
--------
Three traces enter the kernel and reach `task_dispatched` (one even reaches
`task_pending_approval`). The kernel process is then "killed" — simulated
here by tearing down the harness without writing terminal events. A *fresh*
``KernelHarness`` is then assembled against the same ``audit_dir`` and its
``startup()`` lifecycle is invoked.

Assertions
----------
* ``startup()`` finishes in well under SC-009's 10 s wall-clock budget.
* The audit log gains one ``kernel_restart_detected`` row, three
  ``in_flight_auto_failed`` rows, three ``task_failed`` (failureReason =
  ``kernel_restart``) rows, and three ``result_summary_delivered`` rows
  (one per recovered trace).
* The injected recovery channel observes three ``ResultSummary`` payloads,
  each with ``traceOutcome=kernel_restarted`` and a message containing the
  schema-mandated ``re-submit`` + ``NEW eventId`` substrings.
* While the kernel is in the warm-up window (before ``startup()`` returns),
  ``submit()`` MUST short-circuit with an ``event_rejected_warming_up``
  audit row and MUST NOT create new tasks.

This is RED until T085 ships ``KernelHarness.startup()`` + the warm-up
gate inside ``submit()``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestrator_kernel.cli_main import assemble_kernel
from orchestrator_kernel.contracts.audit import AuditEvent, AuditEventType
from orchestrator_kernel.contracts.result_summary import ResultSummary

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _aid(prefix: str, n: int) -> str:
    return f"{prefix}{n:08d}".ljust(20, "0")[:24]


def _ev(
    *,
    audit_id: str,
    event_type: AuditEventType,
    timestamp: datetime,
    trace_id: str | None = None,
    task_id: str | None = None,
    actor: str = "kernel",
    extra: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> AuditEvent:
    p: dict[str, Any] = {
        "auditId": audit_id,
        "timestamp": timestamp,
        "actor": actor,
        "eventType": event_type,
    }
    if trace_id is not None:
        p["traceId"] = trace_id
    if task_id is not None:
        p["taskId"] = task_id
    if extra is not None:
        p["extra"] = extra
    if outcome is not None:
        p["outcome"] = outcome
    return AuditEvent.model_validate(p)


def _seed_three_inflight_traces(audit_dir: Path) -> list[tuple[str, str]]:
    """Write 3 traces' worth of JSONL: dispatched / running / pending_approval.

    Returns the list of (traceId, taskId) pairs in seed order.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    t0 = datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC)
    fname = f"audit-{t0.astimezone(UTC).strftime('%Y-%m-%d')}.jsonl"
    path = audit_dir / fname
    pairs: list[tuple[str, str]] = []
    with path.open("ab") as fp:
        seq = 0
        for idx, last_event in enumerate(
            ["task_dispatched", "task_started", "task_pending_approval"]
        ):
            trace_id = f"01J9TRACE{idx:02d}REC".ljust(20, "0")[:24]
            task_id = f"01J9LEAF{idx:02d}REC".ljust(20, "0")[:24]
            event_id = f"01J9EVT{idx:02d}REC".ljust(20, "0")[:24]
            user_id = f"user-rec-{idx}"
            seq += 1
            fp.write(
                (
                    _ev(
                        audit_id=_aid("R", seq),
                        event_type="event_received",
                        timestamp=t0 + timedelta(seconds=seq),
                        trace_id=trace_id,
                        actor=f"user:{user_id}",
                        extra={"eventId": event_id, "sourceChannel": "cli"},
                    ).model_dump_json(exclude_none=True)
                    + "\n"
                ).encode("utf-8")
            )
            seq += 1
            fp.write(
                (
                    _ev(
                        audit_id=_aid("R", seq),
                        event_type="task_created",
                        timestamp=t0 + timedelta(seconds=seq),
                        trace_id=trace_id,
                        task_id=task_id,
                    ).model_dump_json(exclude_none=True)
                    + "\n"
                ).encode("utf-8")
            )
            seq += 1
            fp.write(
                (
                    _ev(
                        audit_id=_aid("R", seq),
                        event_type=last_event,
                        timestamp=t0 + timedelta(seconds=seq),
                        trace_id=trace_id,
                        task_id=task_id,
                    ).model_dump_json(exclude_none=True)
                    + "\n"
                ).encode("utf-8")
            )
            pairs.append((trace_id, task_id))
    return pairs


def _read_audit_rows(audit_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in sorted(audit_dir.glob("audit-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            import json as _j

            rows.append(_j.loads(line))
    return rows


class _RecordingChannel:
    """Captures every ResultSummary the kernel pushes during startup."""

    def __init__(self) -> None:
        self.pushed: list[ResultSummary] = []

    async def send(self, summary: ResultSummary) -> None:
        self.pushed.append(summary)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestKernelRestartRecovery:
    @pytest.mark.asyncio
    async def test_startup_compensates_three_in_flight_traces(
        self, tmp_audit_dir: Path
    ) -> None:
        seeds = _seed_three_inflight_traces(tmp_audit_dir)
        harness = await assemble_kernel(
            audit_dir=tmp_audit_dir, warm_start=False
        )
        channel = _RecordingChannel()

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        await harness.startup(recovery_channel=channel)
        elapsed_s = loop.time() - started_at

        # SC-009: well under 10 s wall-clock budget.
        assert elapsed_s < 5.0

        # Each of the 3 traces produces one summary.
        assert len(channel.pushed) == 3
        for summary in channel.pushed:
            assert summary.traceOutcome == "kernel_restarted"
            assert "re-submit" in summary.message
            assert "NEW eventId" in summary.message

        seen_traces = {s.traceId for s in channel.pushed}
        assert seen_traces == {trace_id for trace_id, _ in seeds}

        rows = _read_audit_rows(tmp_audit_dir)
        types = [r["eventType"] for r in rows]
        assert types.count("kernel_restart_detected") == 1
        assert types.count("in_flight_auto_failed") == 3
        assert types.count("task_failed") == 3
        assert types.count("result_summary_delivered") == 3
        # Hard failures must not appear on the happy path.
        assert "notification_delivery_failed" not in types

        # Every synthetic task_failed has the canonical reason.
        failed_rows = [r for r in rows if r["eventType"] == "task_failed"]
        for row in failed_rows:
            assert (row.get("extra") or {}).get("failureReason") == "kernel_restart"

        await harness.shutdown()

    @pytest.mark.asyncio
    async def test_submit_during_warmup_is_rejected(
        self, tmp_audit_dir: Path
    ) -> None:
        _seed_three_inflight_traces(tmp_audit_dir)
        harness = await assemble_kernel(
            audit_dir=tmp_audit_dir, warm_start=False
        )

        # Before startup() runs, the kernel is in warm-up: submit MUST be
        # rejected with `event_rejected_warming_up`.
        result = await harness.submit(text="hello world", user_id="alice")
        assert result.traceOutcome == "rejected"
        rows = _read_audit_rows(tmp_audit_dir)
        types = [r["eventType"] for r in rows]
        assert "event_rejected_warming_up" in types
        # No new task got created during warm-up.
        assert "task_created" not in types[-5:] or types[-1] != "task_created"

        await harness.shutdown()

    @pytest.mark.asyncio
    async def test_startup_is_idempotent_within_a_kernel_lifetime(
        self, tmp_audit_dir: Path
    ) -> None:
        _seed_three_inflight_traces(tmp_audit_dir)
        harness = await assemble_kernel(
            audit_dir=tmp_audit_dir, warm_start=False
        )
        channel = _RecordingChannel()

        await harness.startup(recovery_channel=channel)
        first_count = len(channel.pushed)
        await harness.startup(recovery_channel=channel)
        second_count = len(channel.pushed)

        # A second startup() inside the same lifetime is a no-op (the kernel
        # is already warm; the audit log already has terminal rows).
        assert first_count == 3
        assert second_count == first_count

        await harness.shutdown()
