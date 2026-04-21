"""T050 — Idempotency cache unit tests (RED first, GREEN after T051).

Covers spec FR-002 / FR-022 / FR-023 and INV-1 for the pure-data structure
that lives behind ``kernel/idempotency.py``. Tests here do NOT spin up the
full kernel: they exercise the cache directly so we can pin invariants
without depending on the dispatcher / supervisor pipeline.

Shape required by the contract:

* ``cache.lookup_or_register(user_id, event_id) -> (trace_id, is_replay,
  snapshot_or_None)``
  - first call for a given ``(user_id, event_id)`` key returns
    ``(new_trace_id, False, None)``;
  - every subsequent call returns ``(existing_trace_id, True, snapshot)``
    where ``snapshot`` is whatever the caller pushed via
    ``mark_terminal`` (or ``None`` if the trace is still in-flight).
* ``cache.mark_terminal(trace_id, *, trace_outcome, leaves, leaf_outputs,
  delivery_message)`` — stash the terminal-state snapshot so later replays
  can reconstruct a ResultSummary without re-dispatching.
* ``(userId, eventId)`` is the idempotency key. Same eventId for two
  different users is NOT a conflict (spec.md Edge Cases).

Today every assertion fails because ``kernel.idempotency`` does not exist
yet; that is the intended RED signal per Constitution Article VIII.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchestrator_kernel.kernel.idempotency import (
    CachedTrace,
    IdempotencyCache,
)


def test_fresh_submit_mints_new_trace_id() -> None:
    cache = IdempotencyCache()

    trace_id, is_replay, snapshot = cache.lookup_or_register("alice", "E1AAAAAAAAAAAAAAAA")

    assert isinstance(trace_id, str)
    assert len(trace_id) >= 16
    assert is_replay is False
    assert snapshot is None


def test_duplicate_submit_returns_same_trace_id() -> None:
    cache = IdempotencyCache()
    event_id = "E2AAAAAAAAAAAAAAAA"
    first_trace, first_replay, _ = cache.lookup_or_register("alice", event_id)

    second_trace, second_replay, _ = cache.lookup_or_register("alice", event_id)

    assert first_trace == second_trace
    assert first_replay is False
    assert second_replay is True


def test_key_is_user_id_and_event_id_not_just_event_id() -> None:
    """Two users submitting the same eventId MUST get distinct traces."""
    cache = IdempotencyCache()
    event_id = "E3AAAAAAAAAAAAAAAA"

    alice_trace, _, _ = cache.lookup_or_register("alice", event_id)
    bob_trace, bob_replay, _ = cache.lookup_or_register("bob", event_id)

    assert alice_trace != bob_trace
    assert bob_replay is False


def test_replay_exposes_terminal_snapshot_after_mark_terminal() -> None:
    cache = IdempotencyCache()
    event_id = "E4AAAAAAAAAAAAAAAA"
    trace_id, _, _ = cache.lookup_or_register("alice", event_id)

    payload: dict[str, Any] = {
        "traceOutcome": "all_succeeded",
        "leafOutcomes": ["succeeded"],
        "message": "hello",
    }
    cache.mark_terminal(trace_id, snapshot=payload)

    replay_trace, is_replay, snapshot = cache.lookup_or_register("alice", event_id)
    assert replay_trace == trace_id
    assert is_replay is True
    assert isinstance(snapshot, CachedTrace)
    assert snapshot.is_terminal is True
    assert snapshot.trace_outcome == "all_succeeded"
    assert snapshot.snapshot == payload


def test_replay_while_in_flight_returns_no_snapshot() -> None:
    """Before mark_terminal, the replay path still signals is_replay=True but
    exposes snapshot.is_terminal=False so callers can choose to report
    'still running' instead of a stale terminal outcome."""
    cache = IdempotencyCache()
    event_id = "E5AAAAAAAAAAAAAAAA"
    trace_id, _, _ = cache.lookup_or_register("alice", event_id)

    replay_trace, is_replay, snapshot = cache.lookup_or_register("alice", event_id)
    assert replay_trace == trace_id
    assert is_replay is True
    assert isinstance(snapshot, CachedTrace)
    assert snapshot.is_terminal is False


def test_mark_terminal_on_unknown_trace_raises() -> None:
    cache = IdempotencyCache()
    with pytest.raises(KeyError):
        cache.mark_terminal("01ZZZZZZZZZZZZZZZZZZZZZZZZ", snapshot={})


def test_different_event_ids_for_same_user_are_independent() -> None:
    cache = IdempotencyCache()
    a_trace, _, _ = cache.lookup_or_register("alice", "E6AAAAAAAAAAAAAAAA")
    b_trace, b_replay, _ = cache.lookup_or_register("alice", "E7AAAAAAAAAAAAAAAA")

    assert a_trace != b_trace
    assert b_replay is False


def test_concurrent_gather_preserves_key_uniqueness() -> None:
    """50 concurrent lookups on the same (user, event) key MUST agree on one traceId.

    Drives the ``anyio.Lock`` contract: even under contention no second
    traceId is minted, and exactly one caller reports ``is_replay=False``.
    """

    async def _driver() -> tuple[set[str], list[bool]]:
        cache = IdempotencyCache()
        event_id = "E8AAAAAAAAAAAAAAAA"

        async def _one() -> tuple[str, bool]:
            tid, is_replay, _ = cache.lookup_or_register("alice", event_id)
            return tid, is_replay

        results = await asyncio.gather(*(_one() for _ in range(50)))
        trace_ids = {r[0] for r in results}
        replay_flags = [r[1] for r in results]
        return trace_ids, replay_flags

    trace_ids, replay_flags = asyncio.run(_driver())
    assert len(trace_ids) == 1, f"expected 1 unique traceId, got {trace_ids!r}"
    assert replay_flags.count(False) == 1
    assert replay_flags.count(True) == 49
