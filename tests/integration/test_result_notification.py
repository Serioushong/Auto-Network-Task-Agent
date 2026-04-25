"""T080 — RED integration test for ResultSummary delivery + retry (US6 / FR-029 / SC-010).

Drives the **notifier delivery layer** (T084 GREEN target:
``orchestrator_kernel.notifier.delivery.deliver``) end-to-end against a
deterministic flaky channel. Verifies:

  * **SC-010 first-attempt success rate ≥ 99%** across 50 ResultSummary
    payloads with a 1-in-50 (= 2%) injected jitter (we then assert ≥ 96%
    succeeded on attempt 0 — the SC-010 wording's "≥ 99%" applies to a
    *real* channel; with deterministic injected failures we lower the bar
    proportionally to the injection rate).
  * **Retry path reaches 100%** — every initially-failing summary
    eventually succeeds within the 3-retry budget.
  * **Audit pattern** — each retry path emits exactly one
    ``result_summary_retrying`` row per failed attempt, plus the eventual
    ``result_summary_delivered`` row.
  * **Hard-failure path** — when a channel keeps failing past the 3-retry
    cap, the deliverer writes ``notification_delivery_failed`` and surfaces
    the failure to the caller (does NOT silently swallow it).

The test injects a ``sleep`` shim so the [0, 1, 4, 16] s backoff runs in
zero wall-clock time. It asserts that the *intended* delays were observed
(via the shim's call log) so an accidental backoff regression is caught.

RED until ``orchestrator_kernel.notifier.delivery.deliver`` exists.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from orchestrator_kernel.audit.writer import AuditWriter
from orchestrator_kernel.contracts.result_summary import (
    LeafResult,
    ResultSummary,
)

# RED guard — the delivery module ships in T084 GREEN.
delivery = pytest.importorskip(
    "orchestrator_kernel.notifier.delivery",
    reason="T084 has not landed notifier/delivery.py yet (this test is RED).",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _summary(idx: int, *, attempt: int = 0) -> ResultSummary:
    """Make a ResultSummary that exercises the schema's full required set."""
    trace_id = f"01J9TRACENTF{idx:08d}".ljust(20, "0")[:24]
    leaf_id = f"01J9LEAFNTF{idx:08d}".ljust(20, "0")[:24]
    event_id = f"01J9EVTNTFY{idx:08d}".ljust(20, "0")[:24]
    return ResultSummary(
        kind="result_summary",
        traceId=trace_id,
        eventId=event_id,
        userId=f"user-{idx}",
        commandDigest=f"hello-{idx}",
        traceOutcome="all_succeeded",
        leafResults=[
            LeafResult(
                taskId=leaf_id,
                capability="echo",
                outcome="succeeded",
            )
        ],
        message="all leaf tasks succeeded",
        preparedAt=datetime(2026, 4, 25, 9, 0, idx % 60, tzinfo=UTC),
        deliveryAttempt=attempt,
    )


class FlakyChannel:
    """Deterministic failure injector that fails the first N tries per summary."""

    def __init__(self, *, fails_per_summary: dict[str, int]) -> None:
        # trace_id -> remaining failure budget for this summary.
        self._fails = dict(fails_per_summary)
        self.delivered: list[ResultSummary] = []
        self.attempts_log: list[tuple[str, int]] = []

    async def send(self, summary: ResultSummary) -> None:
        self.attempts_log.append((summary.traceId, summary.deliveryAttempt))
        budget = self._fails.get(summary.traceId, 0)
        if budget > 0:
            self._fails[summary.traceId] = budget - 1
            raise ConnectionError(
                f"injected jitter on {summary.traceId} attempt={summary.deliveryAttempt}"
            )
        self.delivered.append(summary)


class _SleepRecorder:
    """Records every requested delay so we can assert backoff shape."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(float(seconds))
        await asyncio.sleep(0)  # yield, do not actually wait


def _read_audit_jsonl(audit_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in sorted(audit_dir.glob("audit-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            import json as _j

            rows.append(_j.loads(line))
    return rows


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestSc010Notification:
    @pytest.mark.asyncio
    async def test_50_traces_first_pass_high_then_100pct_after_retry(
        self, tmp_audit_dir: Path
    ) -> None:
        """SC-010 happy path: 50 summaries, 1 jitter, retry to 100%."""
        n = 50
        summaries = [_summary(i) for i in range(n)]
        # Inject one fail-once on a single trace (~2% failure rate) so a
        # regression that disables the retry path will surface immediately.
        flake_target = summaries[7].traceId
        channel = FlakyChannel(fails_per_summary={flake_target: 1})

        writer = AuditWriter(
            tmp_audit_dir,
            clock=lambda: datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC),
        )
        sleep = _SleepRecorder()

        results = []
        for s in summaries:
            attempts = await delivery.deliver(
                s, channel, audit=writer, sleep=sleep,
            )
            results.append(attempts)

        # 100% delivered eventually.
        assert len(channel.delivered) == n

        # >= 96% succeeded on attempt 0 (= 1 retry across 50 = 49/50 = 98%).
        first_attempt_successes = sum(
            1 for atts in results if atts and atts[0].succeeded
        )
        assert first_attempt_successes >= int(n * 0.96), (
            f"first-pass success rate too low: {first_attempt_successes}/{n}"
        )

        # The flaked trace took exactly 2 attempts.
        flake_attempts = [
            atts for atts, s in zip(results, summaries, strict=False)
            if s.traceId == flake_target
        ][0]
        assert len(flake_attempts) == 2
        assert flake_attempts[0].succeeded is False
        assert flake_attempts[1].succeeded is True

    @pytest.mark.asyncio
    async def test_retry_uses_0_1_4_16_backoff(self, tmp_audit_dir: Path) -> None:
        """The required FR-029 / T084 backoff sequence is observed exactly."""
        s = _summary(99)
        # Force 3 failures so we exercise all 4 attempt slots.
        channel = FlakyChannel(fails_per_summary={s.traceId: 3})
        writer = AuditWriter(
            tmp_audit_dir,
            clock=lambda: datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC),
        )
        sleep = _SleepRecorder()

        attempts = await delivery.deliver(s, channel, audit=writer, sleep=sleep)

        # 4 attempts with the canonical backoff schedule.
        assert sleep.delays == [0.0, 1.0, 4.0, 16.0]
        assert len(attempts) == 4
        assert [a.succeeded for a in attempts] == [False, False, False, True]

    @pytest.mark.asyncio
    async def test_audit_emits_retrying_then_delivered(
        self, tmp_audit_dir: Path
    ) -> None:
        s = _summary(101)
        # Fail once so we get one `retrying` then one `delivered`.
        channel = FlakyChannel(fails_per_summary={s.traceId: 1})
        writer = AuditWriter(
            tmp_audit_dir,
            clock=lambda: datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC),
        )
        sleep = _SleepRecorder()

        await delivery.deliver(s, channel, audit=writer, sleep=sleep)

        rows = _read_audit_jsonl(tmp_audit_dir)
        types = [r["eventType"] for r in rows]
        assert types.count("result_summary_retrying") == 1
        assert types.count("result_summary_delivered") == 1
        assert "notification_delivery_failed" not in types

    @pytest.mark.asyncio
    async def test_hard_failure_writes_notification_delivery_failed_and_raises(
        self, tmp_audit_dir: Path
    ) -> None:
        s = _summary(202)
        # Fail more times than the max-retry budget allows (4 attempts: 0..3).
        channel = FlakyChannel(fails_per_summary={s.traceId: 99})
        writer = AuditWriter(
            tmp_audit_dir,
            clock=lambda: datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC),
        )
        sleep = _SleepRecorder()

        with pytest.raises(delivery.DeliveryFailedError) as excinfo:
            await delivery.deliver(s, channel, audit=writer, sleep=sleep)

        assert excinfo.value.summary.traceId == s.traceId
        assert len(excinfo.value.attempts) == 4
        assert all(not a.succeeded for a in excinfo.value.attempts)

        rows = _read_audit_jsonl(tmp_audit_dir)
        types = [r["eventType"] for r in rows]
        # 3 retrying + 1 final fail = 4 audit entries on the failure side.
        assert types.count("result_summary_retrying") == 3
        assert types.count("notification_delivery_failed") == 1
        assert "result_summary_delivered" not in types

    @pytest.mark.asyncio
    async def test_attempt_field_increments_on_summary_payload(
        self, tmp_audit_dir: Path
    ) -> None:
        """Each retry must mutate ResultSummary.deliveryAttempt 0 → 1 → 2 → 3."""
        s = _summary(303)
        channel = FlakyChannel(fails_per_summary={s.traceId: 2})
        writer = AuditWriter(
            tmp_audit_dir,
            clock=lambda: datetime(2026, 4, 25, 9, 0, 0, tzinfo=UTC),
        )
        sleep = _SleepRecorder()

        await delivery.deliver(s, channel, audit=writer, sleep=sleep)

        # Channel must have observed deliveryAttempt = 0, 1, 2.
        attempts_seen = [
            attempt
            for trace_id, attempt in channel.attempts_log
            if trace_id == s.traceId
        ]
        assert attempts_seen == [0, 1, 2]
