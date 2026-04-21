"""Idempotency cache (T051 — GREEN for Phase 4 US2).

Enforces INV-1: "每条 EntryEvent 在内核视角下有且只有一个 Trace"
(spec.md §P2, data-model.md §F).

Design
------
* Key is the pair ``(userId, eventId)``. Same ``eventId`` for two
  different users MUST mint two separate traces (spec.md Edge Cases).
* First submit → atomically allocate a new ULID traceId, stash an
  ``in_flight`` entry, return ``(trace_id, is_replay=False, snapshot=None)``.
* Repeat submit for the same key → return the original traceId with
  ``is_replay=True`` and the current ``CachedTrace`` snapshot (which is
  ``in_flight`` until ``mark_terminal`` fires, ``terminal`` afterwards).
* Thread-safety: a ``threading.Lock`` guards the compound check-and-set.
  Even under an ``asyncio.gather`` fan-out where 50+ coroutines race for
  the same key, the lock ensures the check-then-insert is atomic so only
  one caller observes ``is_replay=False``.

The cache is *in-memory only*; FR-023 demands reconstruction from the
audit journal on restart (R-03). That reconstruction path is Phase 7's
business and is intentionally out of scope here — we expose a minimal
``restore_entry`` hook that later phases can call while replaying the
audit log.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ulid

__all__ = ["CachedTrace", "IdempotencyCache"]


@dataclass(frozen=True)
class CachedTrace:
    """Snapshot returned to replay callers.

    ``snapshot`` is caller-defined; the cache only remembers the bytes
    verbatim so the notifier layer can rebuild a ResultSummary without
    re-dispatching any Worker (FR-022).
    """

    trace_id: str
    is_terminal: bool
    trace_outcome: str | None = None
    snapshot: dict[str, Any] | None = None


@dataclass
class _Entry:
    trace_id: str
    is_terminal: bool = False
    trace_outcome: str | None = None
    snapshot: dict[str, Any] | None = None

    def as_cached(self) -> CachedTrace:
        return CachedTrace(
            trace_id=self.trace_id,
            is_terminal=self.is_terminal,
            trace_outcome=self.trace_outcome,
            snapshot=self.snapshot,
        )


def _new_trace_id() -> str:
    return str(ulid.new().str)


class IdempotencyCache:
    """In-memory idempotency cache keyed by ``(user_id, event_id)``.

    The public surface is *synchronous*. Every compound operation is
    protected by a ``threading.Lock`` so concurrent asyncio coroutines
    (or future multi-thread callers) cannot race past the existence
    check.
    """

    def __init__(self, *, trace_id_factory: Callable[[], str] | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._by_trace: dict[str, tuple[str, str]] = {}
        self._mint = trace_id_factory or _new_trace_id

    def lookup_or_register(
        self, user_id: str, event_id: str
    ) -> tuple[str, bool, CachedTrace | None]:
        """Atomically look up or create an entry for ``(user_id, event_id)``.

        Returns ``(trace_id, is_replay, snapshot)`` where ``snapshot`` is
        ``None`` on the first call and a ``CachedTrace`` (possibly still
        ``is_terminal=False``) on every subsequent call.
        """
        key = (user_id, event_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return entry.trace_id, True, entry.as_cached()
            trace_id = self._mint()
            self._entries[key] = _Entry(trace_id=trace_id)
            self._by_trace[trace_id] = key
            return trace_id, False, None

    def mark_terminal(
        self,
        trace_id: str,
        *,
        snapshot: dict[str, Any],
        trace_outcome: str | None = None,
    ) -> None:
        """Promote the entry for ``trace_id`` to the terminal snapshot.

        Raises ``KeyError`` if the trace was never registered; callers
        that need a "best-effort" path should catch it explicitly.
        """
        with self._lock:
            key = self._by_trace.get(trace_id)
            if key is None:
                raise KeyError(trace_id)
            entry = self._entries[key]
            outcome = trace_outcome
            if outcome is None and isinstance(snapshot, dict):
                maybe = snapshot.get("traceOutcome")
                if isinstance(maybe, str):
                    outcome = maybe
            entry.is_terminal = True
            entry.trace_outcome = outcome
            entry.snapshot = snapshot

    def restore_entry(
        self,
        *,
        user_id: str,
        event_id: str,
        trace_id: str,
        is_terminal: bool,
        trace_outcome: str | None,
        snapshot: dict[str, Any] | None,
    ) -> None:
        """Recreate a cache entry verbatim — used by audit-scan replay (R-03).

        Intentionally permissive: if the entry already exists we upgrade
        its state only when the incoming record is terminal; otherwise
        we keep whatever is already in memory to avoid regressing a
        live entry back into ``in_flight``.
        """
        key = (user_id, event_id)
        with self._lock:
            existing = self._entries.get(key)
            if existing is None:
                self._entries[key] = _Entry(
                    trace_id=trace_id,
                    is_terminal=is_terminal,
                    trace_outcome=trace_outcome,
                    snapshot=snapshot,
                )
                self._by_trace[trace_id] = key
                return
            if is_terminal and not existing.is_terminal:
                existing.is_terminal = True
                existing.trace_outcome = trace_outcome
                existing.snapshot = snapshot

    def __contains__(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return key in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
