"""T084 — Retrying ResultSummary delivery (Phase 8 / US6 / FR-029).

Why
---
``notifier.result_summary.print_to_cli`` (T046) handles single-shot writes
to the CLI channel. FR-029 / SC-010 demand a real **delivery loop** that:

  * Tries the source channel up to **4 times** (initial + 3 retries) using
    the canonical exponential backoff ``[0, 1, 4, 16]`` seconds.
  * Mutates ``ResultSummary.deliveryAttempt`` so each call observes the
    correct counter (contract: 0 = first delivery, max 3).
  * Audits **every transition**: ``result_summary_retrying`` per failed
    attempt, ``result_summary_delivered`` on success,
    ``notification_delivery_failed`` if all 4 attempts fail.
  * Surfaces hard failures by raising :class:`DeliveryFailedError` —
    silently swallowing failures would let traces appear "successfully
    delivered" while in reality the user never got the push.

Design notes
------------
* The retry loop honours INV-7: every trace ends with at least one
  ``result_summary_delivered`` *or* one ``notification_delivery_failed``
  audit row.
* The ``sleep`` and ``clock`` callables are dependency-injected so unit
  tests can fast-forward time without touching ``asyncio.sleep`` directly.
* The ``DeliveryChannel`` Protocol is intentionally minimal so any sink —
  CLI stdout, HTTP webhook (T097), Feishu stub (T098) — can satisfy it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

import ulid

from ..contracts.audit import AuditEvent
from ..contracts.result_summary import ResultSummary

__all__ = [
    "DEFAULT_BACKOFF_SECONDS",
    "MAX_DELIVERY_ATTEMPTS",
    "DeliveryAttempt",
    "DeliveryChannel",
    "DeliveryFailedError",
    "deliver",
]

# 4 attempts (initial + 3 retries) per FR-029 / R-09. ``deliveryAttempt`` on
# the contract is capped at 3 (= last retry index), see schema description.
DEFAULT_BACKOFF_SECONDS: Final[tuple[float, ...]] = (0.0, 1.0, 4.0, 16.0)
MAX_DELIVERY_ATTEMPTS: Final[int] = len(DEFAULT_BACKOFF_SECONDS)


class DeliveryChannel(Protocol):
    """Minimal sink contract for any push channel.

    Implementations MUST raise on transport / serialization failure so the
    deliverer can drive the retry loop. Returning normally MUST mean the
    payload is durably handed off (the channel's responsibility — e.g. a
    flushed stdout, a 2xx HTTP response, an acked webhook).
    """

    async def send(self, summary: ResultSummary) -> None: ...


@dataclass(frozen=True)
class DeliveryAttempt:
    """Per-attempt outcome record returned to the caller for observability."""

    attempt: int
    delay_seconds: float
    succeeded: bool
    error: str | None
    at: datetime


class DeliveryFailedError(RuntimeError):
    """Raised after all retries exhaust without a successful send.

    Carries the final summary state and the full attempt log so callers
    (T086 normal-path wiring) can decide whether to crash, surface a CLI
    warning, or escalate to operator notification.
    """

    def __init__(
        self,
        summary: ResultSummary,
        attempts: Sequence[DeliveryAttempt],
    ) -> None:
        super().__init__(
            f"delivery exhausted {len(attempts)} attempts for trace "
            f"{summary.traceId!r}"
        )
        self.summary = summary
        self.attempts = tuple(attempts)


# --------------------------------------------------------------------------
# deliver() — main loop
# --------------------------------------------------------------------------


async def deliver(
    summary: ResultSummary,
    channel: DeliveryChannel,
    *,
    audit: object | None = None,  # AuditWriter (typed loosely to avoid cycle)
    backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> list[DeliveryAttempt]:
    """Send ``summary`` over ``channel`` with exponential backoff + audit.

    Args:
        summary: ResultSummary to deliver. Caller MAY pre-set
            ``deliveryAttempt=0`` (the deliverer rebuilds the field on each
            retry anyway).
        channel: any object satisfying :class:`DeliveryChannel`.
        audit: optional ``AuditWriter`` to receive
            ``result_summary_retrying`` / ``result_summary_delivered`` /
            ``notification_delivery_failed`` rows.
        backoff_seconds: delay before each attempt. Length determines the
            total attempt count (default 4 = ``[0, 1, 4, 16]``).
        sleep: dependency-injected sleeper for tests; defaults to
            ``asyncio.sleep``.
        clock: timestamp source; defaults to ``datetime.now(tz=UTC)``.

    Returns:
        List of :class:`DeliveryAttempt` describing every attempt that was
        made (including the successful one).

    Raises:
        DeliveryFailedError: if every attempt failed.
    """
    sleeper = sleep or asyncio.sleep
    now: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    attempts: list[DeliveryAttempt] = []
    last_error: BaseException | None = None
    total_slots = len(backoff_seconds)

    for attempt_idx, delay in enumerate(backoff_seconds):
        await sleeper(delay)

        # Mutate deliveryAttempt to reflect the *current* try (contract
        # requires the field to track the live attempt counter). Using
        # model_copy keeps validators in the loop, e.g. for the
        # `kernel_restarted` message hint.
        clamped = min(attempt_idx, MAX_DELIVERY_ATTEMPTS - 1)
        is_last = attempt_idx == total_slots - 1
        try:
            summary = summary.model_copy(update={"deliveryAttempt": clamped})
        except Exception as exc:  # noqa: BLE001 — pydantic validators may surface here
            last_error = exc
            attempts.append(
                DeliveryAttempt(
                    attempt=attempt_idx,
                    delay_seconds=delay,
                    succeeded=False,
                    error=f"summary_mutation_failed: {exc!r}",
                    at=now(),
                )
            )
            # Only emit `retrying` if another attempt will follow; the
            # final attempt's failure is captured by `notification_delivery_failed`.
            if not is_last:
                _audit_retry(audit, summary, attempts[-1], now)
            continue

        try:
            await channel.send(summary)
        except Exception as exc:  # noqa: BLE001 — any transport failure should retry
            last_error = exc
            attempts.append(
                DeliveryAttempt(
                    attempt=attempt_idx,
                    delay_seconds=delay,
                    succeeded=False,
                    error=repr(exc),
                    at=now(),
                )
            )
            if not is_last:
                _audit_retry(audit, summary, attempts[-1], now)
            continue

        attempts.append(
            DeliveryAttempt(
                attempt=attempt_idx,
                delay_seconds=delay,
                succeeded=True,
                error=None,
                at=now(),
            )
        )
        _audit_delivered(audit, summary, attempts[-1], now)
        return attempts

    _audit_hard_failure(audit, summary, attempts, now, last_error)
    raise DeliveryFailedError(summary, attempts)


# --------------------------------------------------------------------------
# Audit helpers — kept private so callers do not assemble events themselves.
# --------------------------------------------------------------------------


def _audit_retry(
    audit: object | None,
    summary: ResultSummary,
    attempt: DeliveryAttempt,
    now: Callable[[], datetime],
) -> None:
    if audit is None:
        return
    event = AuditEvent.model_validate(
        {
            "auditId": _new_id(),
            "timestamp": now(),
            "actor": "kernel",
            "eventType": "result_summary_retrying",
            "traceId": summary.traceId,
            "extra": {
                "deliveryAttempt": attempt.attempt,
                "delaySeconds": attempt.delay_seconds,
                "error": attempt.error,
                "userId": summary.userId,
                "eventId": summary.eventId,
            },
        }
    )
    _safe_write(audit, event)


def _audit_delivered(
    audit: object | None,
    summary: ResultSummary,
    attempt: DeliveryAttempt,
    now: Callable[[], datetime],
) -> None:
    if audit is None:
        return
    event = AuditEvent.model_validate(
        {
            "auditId": _new_id(),
            "timestamp": now(),
            "actor": "kernel",
            "eventType": "result_summary_delivered",
            "traceId": summary.traceId,
            "outcome": "succeeded",
            "extra": {
                "deliveryAttempt": attempt.attempt,
                "userId": summary.userId,
                "eventId": summary.eventId,
                "traceOutcome": summary.traceOutcome,
            },
        }
    )
    _safe_write(audit, event)


def _audit_hard_failure(
    audit: object | None,
    summary: ResultSummary,
    attempts: Sequence[DeliveryAttempt],
    now: Callable[[], datetime],
    last_error: BaseException | None,
) -> None:
    if audit is None:
        return
    event = AuditEvent.model_validate(
        {
            "auditId": _new_id(),
            "timestamp": now(),
            "actor": "kernel",
            "eventType": "notification_delivery_failed",
            "traceId": summary.traceId,
            "outcome": "failed",
            "extra": {
                "totalAttempts": len(attempts),
                "lastError": repr(last_error) if last_error else None,
                "userId": summary.userId,
                "eventId": summary.eventId,
                "traceOutcome": summary.traceOutcome,
            },
        }
    )
    _safe_write(audit, event)


def _safe_write(audit: object, event: AuditEvent) -> None:
    """Best-effort audit write — must NEVER mask delivery exceptions."""
    write = getattr(audit, "write", None)
    if write is None:
        return
    try:
        write(event)
    except Exception:  # noqa: BLE001 — audit MUST NOT take down the deliverer
        pass


def _new_id() -> str:
    return str(ulid.new().str)
