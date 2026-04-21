"""T034 — failing unit test for the 4-dimensional rate limiter.

RED until T035 ships `orchestrator_kernel.kernel.rate_limit.RateLimiter`.

Dimensions (spec.md 速率限制与并发控制, FR-026 / FR-027):
    global_rps=50          — token bucket, refills 50 tokens/sec
    user_rpm=120           — sliding window, per userId
    user_concurrent=10     — inflight counter, per userId
    user_highrisk_concurrent=1  — per userId, HIGH_RISK leaves

Each rejection MUST surface an `AuditEvent` of type
`event_rejected_rate_limited` whose `extra.dimension` field identifies which
of the four gates tripped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orchestrator_kernel.kernel.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    RateLimits,
)


def _now() -> datetime:
    return datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


def _mk_limiter(**overrides: int) -> tuple[RateLimiter, list[datetime]]:
    limits = RateLimits(
        global_rps=overrides.get("global_rps", 50),
        user_rpm=overrides.get("user_rpm", 120),
        user_concurrent=overrides.get("user_concurrent", 10),
        user_highrisk_concurrent=overrides.get("user_highrisk_concurrent", 1),
    )
    clock_now = _now()
    ticks: list[datetime] = [clock_now]

    def _clock() -> datetime:
        return ticks[0]

    return RateLimiter(limits, clock=_clock), ticks


class TestGlobalRps:
    def test_first_50_admitted(self) -> None:
        rl, _ = _mk_limiter()
        for i in range(50):
            d = rl.try_admit(user_id=f"u{i}", risk_level="NORMAL")
            assert d.admitted, f"event #{i} unexpectedly rejected: {d}"

    def test_51st_rejected_with_global_dimension(self) -> None:
        # Use 50 distinct userIds so user_concurrent (<=10) never trips first.
        rl, _ = _mk_limiter()
        for i in range(50):
            d = rl.try_admit(user_id=f"u{i}", risk_level="NORMAL")
            assert d.admitted
        d = rl.try_admit(user_id="u-overflow", risk_level="NORMAL")
        assert not d.admitted
        assert d.dimension == "global_rps"

    def test_refills_after_one_second(self) -> None:
        rl, ticks = _mk_limiter()
        for i in range(50):
            rl.try_admit(user_id=f"u{i}", risk_level="NORMAL")
        # Advance clock 1s; global bucket refills fully.
        ticks[0] = ticks[0] + timedelta(seconds=1)
        d = rl.try_admit(user_id="u-new", risk_level="NORMAL")
        assert d.admitted


class TestUserRpm:
    def test_first_120_from_same_user_admitted(self) -> None:
        # Spread across 3 seconds so global_rps (50/s) is not the bottleneck.
        rl, ticks = _mk_limiter()
        for bucket in range(3):
            ticks[0] = _now() + timedelta(seconds=bucket)
            for _ in range(40):
                d = rl.try_admit(user_id="alice", risk_level="NORMAL")
                rl.release(user_id="alice", risk_level="NORMAL")
                assert d.admitted

    def test_121st_from_same_user_within_minute_rejected(self) -> None:
        rl, ticks = _mk_limiter()
        for bucket in range(3):
            ticks[0] = _now() + timedelta(seconds=bucket)
            for _ in range(40):
                rl.try_admit(user_id="alice", risk_level="NORMAL")
                rl.release(user_id="alice", risk_level="NORMAL")
        ticks[0] = _now() + timedelta(seconds=3)
        d = rl.try_admit(user_id="alice", risk_level="NORMAL")
        assert not d.admitted
        assert d.dimension == "user_rpm"


class TestUserConcurrent:
    def test_10_concurrent_admitted(self) -> None:
        rl, _ = _mk_limiter()
        for _ in range(10):
            d = rl.try_admit(user_id="alice", risk_level="NORMAL")
            assert d.admitted

    def test_11th_concurrent_rejected_with_concurrent_dimension(self) -> None:
        rl, _ = _mk_limiter()
        for _ in range(10):
            rl.try_admit(user_id="alice", risk_level="NORMAL")
        d = rl.try_admit(user_id="alice", risk_level="NORMAL")
        assert not d.admitted
        assert d.dimension == "user_concurrent"

    def test_release_frees_slot(self) -> None:
        rl, _ = _mk_limiter()
        for _ in range(10):
            rl.try_admit(user_id="alice", risk_level="NORMAL")
        rl.release(user_id="alice", risk_level="NORMAL")
        d = rl.try_admit(user_id="alice", risk_level="NORMAL")
        assert d.admitted


class TestUserHighRiskConcurrent:
    def test_one_high_risk_at_a_time(self) -> None:
        rl, _ = _mk_limiter()
        d1 = rl.try_admit(user_id="alice", risk_level="HIGH_RISK")
        assert d1.admitted
        d2 = rl.try_admit(user_id="alice", risk_level="HIGH_RISK")
        assert not d2.admitted
        assert d2.dimension == "user_highrisk_concurrent"

    def test_normal_parallel_to_highrisk_ok(self) -> None:
        rl, _ = _mk_limiter()
        rl.try_admit(user_id="alice", risk_level="HIGH_RISK")
        d = rl.try_admit(user_id="alice", risk_level="NORMAL")
        assert d.admitted

    def test_highrisk_release_frees_slot(self) -> None:
        rl, _ = _mk_limiter()
        rl.try_admit(user_id="alice", risk_level="HIGH_RISK")
        rl.release(user_id="alice", risk_level="HIGH_RISK")
        d = rl.try_admit(user_id="alice", risk_level="HIGH_RISK")
        assert d.admitted


class TestDecisionShape:
    def test_admitted_decision_has_no_dimension(self) -> None:
        rl, _ = _mk_limiter()
        d = rl.try_admit(user_id="alice", risk_level="NORMAL")
        assert isinstance(d, RateLimitDecision)
        assert d.admitted is True
        assert d.dimension is None

    @pytest.mark.parametrize(
        "dim",
        [
            "global_rps",
            "user_rpm",
            "user_concurrent",
            "user_highrisk_concurrent",
        ],
    )
    def test_rejection_dimension_is_one_of_four(self, dim: str) -> None:
        assert dim in RateLimits.DIMENSION_NAMES
