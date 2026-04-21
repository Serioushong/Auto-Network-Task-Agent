"""T035 — 4-dimensional RateLimiter (FR-026 / FR-027).

Dimensions, checked in fixed order per request:

  1. global_rps            — token bucket, deploy-wide (spec default 50 / s)
  2. user_rpm              — sliding window (60 s) per userId (default 120)
  3. user_concurrent       — inflight counter per userId (default 10)
  4. user_highrisk_concurrent — inflight HIGH_RISK counter per userId (default 1)

The `try_admit(user_id, risk_level)` API returns a RateLimitDecision; on
admission the caller MUST later pair it with `release(user_id, risk_level)`.

Concurrency: every mutation is serialised by a single in-process `anyio.Lock`
so the counters stay consistent under a many-task supervision group. For MVP
we assume a single-process kernel; horizontal scaling requires swapping this
out for a shared-store implementation.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal

RiskLevel = Literal["NORMAL", "HIGH_RISK"]
DimensionName = Literal[
    "global_rps",
    "user_rpm",
    "user_concurrent",
    "user_highrisk_concurrent",
]


@dataclass(frozen=True)
class RateLimits:
    """Deploy-time configuration knob for the four rate gates."""

    global_rps: int = 50
    user_rpm: int = 120
    user_concurrent: int = 10
    user_highrisk_concurrent: int = 1

    DIMENSION_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"global_rps", "user_rpm", "user_concurrent", "user_highrisk_concurrent"}
    )


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a single `try_admit` call."""

    admitted: bool
    dimension: DimensionName | None = None


@dataclass
class _UserState:
    rpm_timestamps: deque[datetime] = field(default_factory=deque)
    concurrent: int = 0
    highrisk_concurrent: int = 0


class RateLimiter:
    """In-process rate limiter. Synchronous API (kernel wraps it with anyio.Lock)."""

    _WINDOW = timedelta(seconds=60)

    def __init__(
        self,
        limits: RateLimits,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._limits = limits
        self._clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(tz=UTC)
        )
        self._global_tokens: float = float(limits.global_rps)
        self._global_last_refill: datetime = self._clock()
        self._users: dict[str, _UserState] = {}

    def try_admit(
        self, *, user_id: str, risk_level: RiskLevel
    ) -> RateLimitDecision:
        """Attempt to admit one event. Call `release()` when processing finishes."""
        now = self._clock()
        self._refill_global(now)

        if self._global_tokens < 1.0:
            return RateLimitDecision(admitted=False, dimension="global_rps")

        state = self._users.setdefault(user_id, _UserState())
        self._trim_rpm_window(state, now)
        if len(state.rpm_timestamps) >= self._limits.user_rpm:
            return RateLimitDecision(admitted=False, dimension="user_rpm")

        if state.concurrent >= self._limits.user_concurrent:
            return RateLimitDecision(admitted=False, dimension="user_concurrent")

        if (
            risk_level == "HIGH_RISK"
            and state.highrisk_concurrent >= self._limits.user_highrisk_concurrent
        ):
            return RateLimitDecision(
                admitted=False, dimension="user_highrisk_concurrent"
            )

        # All gates passed — commit.
        self._global_tokens -= 1.0
        state.rpm_timestamps.append(now)
        state.concurrent += 1
        if risk_level == "HIGH_RISK":
            state.highrisk_concurrent += 1
        return RateLimitDecision(admitted=True, dimension=None)

    def release(self, *, user_id: str, risk_level: RiskLevel) -> None:
        """Decrement the concurrency counters for a previously-admitted event."""
        state = self._users.get(user_id)
        if state is None:
            return
        if state.concurrent > 0:
            state.concurrent -= 1
        if risk_level == "HIGH_RISK" and state.highrisk_concurrent > 0:
            state.highrisk_concurrent -= 1

    def _refill_global(self, now: datetime) -> None:
        if now <= self._global_last_refill:
            return
        elapsed = (now - self._global_last_refill).total_seconds()
        self._global_tokens = min(
            float(self._limits.global_rps),
            self._global_tokens + elapsed * self._limits.global_rps,
        )
        self._global_last_refill = now

    def _trim_rpm_window(self, state: _UserState, now: datetime) -> None:
        cutoff = now - self._WINDOW
        while state.rpm_timestamps and state.rpm_timestamps[0] < cutoff:
            state.rpm_timestamps.popleft()
