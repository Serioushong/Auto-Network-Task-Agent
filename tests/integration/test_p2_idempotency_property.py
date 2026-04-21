"""T049 — Phase 4 US2 RED: hypothesis state-machine over the cache.

Validates INV-1 ("每条 EntryEvent 在内核视角下有且只有一个 Trace") and
the derived invariant "audit replay count = total_submits − unique_keys"
via a ``RuleBasedStateMachine`` that pokes the pure-data
``IdempotencyCache`` directly. Keeping the machine at cache level (not
full kernel) means hypothesis can drive hundreds of shrinking runs per
CI in milliseconds.

Rules:
  * ``submit(user_id, event_id)`` — create a brand-new (user, event) pair.
  * ``duplicate_submit`` — pick an existing pair, call it again.
  * ``mark_terminal`` — flip one in-flight trace to terminal.

Invariants:
  * For every (user_id, event_id) key the cache has ever seen, the
    sequence of returned ``traceId`` values contains exactly one unique
    value.
  * ``total_lookup_calls - count(unique_keys) == count(is_replay=True)``.
  * After ``mark_terminal(trace_id, ...)`` a future ``lookup_or_register``
    on that key MUST expose ``snapshot.is_terminal=True``.
"""

from __future__ import annotations

import pytest
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from hypothesis.strategies import sampled_from

from orchestrator_kernel.kernel.idempotency import IdempotencyCache

USERS = ("alice", "bob", "carol")
EVENT_IDS = tuple(f"E{str(i).zfill(16)}X" for i in range(10))


class IdempotencyMachine(RuleBasedStateMachine):
    cache: IdempotencyCache

    @initialize()
    def _boot(self) -> None:
        self.cache = IdempotencyCache()
        self.trace_for_key: dict[tuple[str, str], str] = {}
        self.replay_count = 0
        self.unique_keys = 0
        self.total_lookups = 0
        self.terminal_traces: set[str] = set()

    @rule(user=sampled_from(USERS), event=sampled_from(EVENT_IDS))
    def submit(self, user: str, event: str) -> None:
        key = (user, event)
        trace_id, is_replay, snapshot = self.cache.lookup_or_register(user, event)
        self.total_lookups += 1
        if key in self.trace_for_key:
            assert is_replay is True
            assert self.trace_for_key[key] == trace_id
            if trace_id in self.terminal_traces:
                assert snapshot is not None and snapshot.is_terminal is True
            self.replay_count += 1
        else:
            assert is_replay is False
            assert snapshot is None
            self.trace_for_key[key] = trace_id
            self.unique_keys += 1

    @rule(user=sampled_from(USERS), event=sampled_from(EVENT_IDS))
    def mark_terminal(self, user: str, event: str) -> None:
        key = (user, event)
        if key not in self.trace_for_key:
            return
        trace_id = self.trace_for_key[key]
        if trace_id in self.terminal_traces:
            return
        self.cache.mark_terminal(
            trace_id, snapshot={"traceOutcome": "all_succeeded"}
        )
        self.terminal_traces.add(trace_id)

    @invariant()
    def replay_accounting_matches(self) -> None:
        assert self.total_lookups - self.unique_keys == self.replay_count

    @invariant()
    def keys_remain_unique(self) -> None:
        trace_ids = list(self.trace_for_key.values())
        assert len(trace_ids) == len(set(trace_ids)), (
            "two distinct (user,event) keys produced the same traceId"
        )


IdempotencyMachine.TestCase.settings = settings(max_examples=50, stateful_step_count=25)


TestIdempotencyMachine = IdempotencyMachine.TestCase  # pytest entrypoint
pytestmark = pytest.mark.integration
