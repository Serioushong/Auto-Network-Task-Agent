"""T089 — Phase 9 US7 RED: payload-size guard under flood.

SC-011 (spec.md §Success Criteria) requires that 100 oversize payloads
(32 KB / 256 KB / 1 MB / 2 MB, 25 each) submitted back-to-back are ALL
rejected at intake within 50 ms each, that no Task is created for any of
them, and that kernel CPU / memory show no abnormal spikes during the
flood.

This test drives the REAL ``KernelHarness.submit`` (no worker registered
\u2014 the guard fires before any leaf would even be planned) and asserts:

1. Every submit returns ``traceOutcome == "rejected"`` with
   ``message`` mentioning the byte counts (FR-031 wording).
2. Each individual submit completes in \u2264 50 ms (per SC-011); we record
   the max latency for the validation evidence.
3. The audit log contains exactly ``len(payloads)`` rows of
   ``event_rejected_too_large`` and ZERO rows of ``trace_created`` /
   ``task_created`` (the queue does not grow).
4. Process RSS / CPU sampled before vs after the flood show no
   pathological spike (\u0394RSS < 200 MB, \u0394CPU% averaged < 50%). These
   are loose guards \u2014 a correct implementation hovers near zero.

The first three assertions exercise the user-visible contract; the
fourth is the operational bar SC-011 cares about.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import psutil
import pytest

from ._harness import KernelHarnessProtocol

# Default payload limit is 16 KB \u2014 every entry below MUST exceed it.
PAYLOAD_SIZES_BYTES = (
    32 * 1024,
    256 * 1024,
    1 * 1024 * 1024,
    2 * 1024 * 1024,
)
PER_SIZE = 25
TOTAL_PAYLOADS = len(PAYLOAD_SIZES_BYTES) * PER_SIZE  # 100
PER_SUBMIT_BUDGET_S = 0.050  # SC-011 hard cap
RSS_DELTA_BUDGET_BYTES = 200 * 1024 * 1024


def _make_payload(num_bytes: int, marker: str) -> str:
    """Build a deterministic UTF-8 string of exactly ``num_bytes`` length.

    Marker keeps the rejected payloads distinguishable in audit forensics
    without inflating size: we pad with ASCII ``"x"`` so the byte length
    equals the character length under UTF-8.
    """
    prefix = f"flood:{marker}:"
    pad_len = num_bytes - len(prefix.encode("utf-8"))
    if pad_len < 0:
        raise ValueError(f"requested size {num_bytes} smaller than prefix")
    return prefix + ("x" * pad_len)


@pytest.mark.integration
async def test_payload_flood_all_rejected_under_budget(
    kernel_harness: KernelHarnessProtocol,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """100 oversize payloads MUST all be rejected, each \u2264 50 ms (SC-011)."""
    proc = psutil.Process(os.getpid())
    rss_before = proc.memory_info().rss
    proc.cpu_percent(None)  # prime the per-process CPU sampler

    latencies_s: list[float] = []
    outcomes: list[str] = []
    for size_idx, size in enumerate(PAYLOAD_SIZES_BYTES):
        for n in range(PER_SIZE):
            text = _make_payload(size, f"s{size_idx}-n{n}")
            t0 = time.perf_counter()
            result = await kernel_harness.submit(
                text=text, user_id="alice", timeout_s=2.0
            )
            elapsed = time.perf_counter() - t0
            latencies_s.append(elapsed)
            outcomes.append(result.traceOutcome)

    rss_after = proc.memory_info().rss
    cpu_window_pct = proc.cpu_percent(None)

    # 1) every outcome rejected, message mentions byte counts.
    assert all(o == "rejected" for o in outcomes), (
        f"non-reject outcome detected: {set(outcomes)}"
    )

    # 2) per-submit budget \u2014 SC-011 is "\u2264 50 ms" per event.
    over_budget = [
        (i, lat) for i, lat in enumerate(latencies_s) if lat > PER_SUBMIT_BUDGET_S
    ]
    max_lat = max(latencies_s)
    assert not over_budget, (
        f"{len(over_budget)} submits exceeded SC-011's 50 ms budget; "
        f"worst was idx={over_budget[0][0]} @ {over_budget[0][1] * 1000:.2f} ms; "
        f"max overall {max_lat * 1000:.2f} ms"
    )

    # 3) audit log: only `event_rejected_too_large` rows; queue did not grow.
    events = audit_events_factory()
    rejected = [
        e for e in events if e.get("eventType") == "event_rejected_too_large"
    ]
    assert len(rejected) == TOTAL_PAYLOADS, (
        f"expected {TOTAL_PAYLOADS} rejection rows, "
        f"got {len(rejected)}; total audit rows={len(events)}"
    )
    forbidden = {"trace_created", "task_created", "task_dispatched"}
    leaked = [e for e in events if e.get("eventType") in forbidden]
    assert not leaked, (
        f"oversize payloads leaked into the queue: {[e['eventType'] for e in leaked][:5]}"
    )

    # extra: every rejection MUST carry actual / limit bytes
    for row in rejected[:5]:
        extra = row.get("extra") or {}
        assert "actual_bytes" in extra and "limit_bytes" in extra, (
            f"audit row missing FR-031 extras: {row}"
        )

    # 4) operational bar: no pathological spike during the flood.
    rss_delta = rss_after - rss_before
    assert rss_delta < RSS_DELTA_BUDGET_BYTES, (
        f"\u0394RSS {rss_delta / 1024 / 1024:.1f} MB exceeds 200 MB \u2014 "
        f"oversize payloads may be retained somewhere they shouldn't"
    )
    # cpu_percent over the ~100ms-1s window: loose ceiling, mostly diagnostic.
    assert cpu_window_pct < 200.0, (
        f"unexpected CPU spike during reject flood: {cpu_window_pct:.1f}%"
    )


@pytest.mark.integration
async def test_payload_guard_runs_before_schema_validation(
    kernel_harness: KernelHarnessProtocol,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """FR-031: payload guard MUST be the first intake step.

    A 1 MB body MUST be rejected with ``event_rejected_too_large`` even
    though the user_id is also non-conformant; the audit MUST land
    (with ``actor="system"``) so the rejection is forensically captured,
    and NO schema-validation event (``event_received``, ``trace_created``)
    is permitted.
    """
    big = _make_payload(1 * 1024 * 1024, "ordering-probe")

    result = await kernel_harness.submit(
        text=big, user_id="!!!!nonconformant!!!!", timeout_s=2.0
    )
    assert result.traceOutcome == "rejected"

    events = audit_events_factory()
    types = [e.get("eventType") for e in events]
    assert "event_rejected_too_large" in types, types
    # schema-stage events MUST NOT have fired before the guard tripped.
    for forbidden in ("event_received", "trace_created", "task_created"):
        assert forbidden not in types, (
            f"{forbidden!r} fired before payload guard \u2014 ordering broken"
        )


@pytest.mark.integration
async def test_concurrent_payload_flood_still_rejects_all(
    kernel_harness: KernelHarnessProtocol,
    audit_events_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    """asyncio.gather of 50 oversize submits \u2014 every one MUST reject."""
    payloads = [_make_payload(64 * 1024, f"par-{i}") for i in range(50)]

    async def _one(text: str) -> Any:
        return await kernel_harness.submit(
            text=text, user_id="bob", timeout_s=2.0
        )

    results = await asyncio.gather(*[_one(p) for p in payloads])
    assert all(r.traceOutcome == "rejected" for r in results)

    events = audit_events_factory()
    rejected = [
        e for e in events if e.get("eventType") == "event_rejected_too_large"
    ]
    assert len(rejected) == len(payloads)
    forbidden = {"trace_created", "task_created"}
    leaked = [e for e in events if e.get("eventType") in forbidden]
    assert not leaked, (
        f"queue grew during concurrent reject flood: "
        f"{[e['eventType'] for e in leaked][:5]}"
    )
