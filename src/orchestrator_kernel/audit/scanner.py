"""T082 — Crash-recovery audit scanner (Phase 8 / US6 / FR-028).

Why
---
The kernel's authoritative state is the on-disk JSONL audit log (FR-006 /
FR-019). When the kernel process dies mid-trace, any Task that was
``pending | pending_approval | dispatched | running`` becomes "in-flight" with
no terminal record on disk. On the next boot the scanner walks those JSONL
files, reconstructs each Task's last observed state, and:

  * writes one ``kernel_restart_detected`` audit row (per scan)
  * writes one ``in_flight_auto_failed`` audit row per affected trace
  * writes one ``task_failed`` (``failureReason=kernel_restart``) per affected
    leaf Task — closing INV-6 and unblocking the FR-029 ResultSummary
    pipeline (T083 / T084 / T085).

Design notes
------------
* The scanner is **read-only** with respect to existing rows; it only
  *appends* compensating rows through the injected ``AuditWriter``. INV-5 is
  preserved because every row keeps its append-only ordering.
* It is **idempotent**: a second invocation on the same directory observes
  the new ``task_failed`` rows and finds zero in-flight tasks, so it returns
  ``[]`` and writes nothing.
* It is **defensive against malformed lines**: a bad JSON line, an
  ``AuditEvent.model_validate`` failure, or a missing required field is
  silently skipped (counted in ``stderr`` only) so a single corrupt row
  cannot wedge boot.
* ``idempotent_replay`` events are *not* state transitions; they're metadata
  about a duplicate ``EntryEvent`` and MUST NOT mark a task as freshly
  in-flight (they carry no taskId in the canonical case).
* ``userId`` and ``eventId`` are best-effort: parsed from the
  ``event_received`` row's ``actor="user:<id>"`` and ``extra.eventId``
  respectively. Absent → ``None`` (T085 still pushes a ResultSummary, but
  the channel resolution falls back to the kernel's default CLI stdout).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import ulid
from pydantic import ValidationError

from ..contracts.audit import AuditEvent, AuditEventType
from ..contracts.task import TERMINAL_STATES, TaskState
from .writer import AuditWriter

__all__ = [
    "AutoFailedTrace",
    "ScannerStats",
    "scan_audit_dir",
    "scan_and_autofail",
]

# --------------------------------------------------------------------------
# Event-type → derived TaskState mapping.
#
# Only events that mutate Task lifecycle state appear here. Approval-flow
# transitions feed `pending_approval`; dispatcher feeds `dispatched`; worker
# feedback feeds `running` / terminal. Any event-type missing from this map
# (e.g. `worker_registered`, `idempotent_replay`, `event_received`) is a
# no-op for state reconstruction.
# --------------------------------------------------------------------------
_EVENT_TO_STATE: Final[dict[AuditEventType, TaskState]] = {
    "task_created": "pending",
    "task_pending_approval": "pending_approval",
    "approval_granted": "dispatched",
    "approval_denied": "denied",
    "approval_timeout": "denied_by_timeout",
    "task_dispatched": "dispatched",
    "task_started": "running",
    "task_succeeded": "succeeded",
    "task_failed": "failed",
    "task_cancelled": "cancelled",
}


# --------------------------------------------------------------------------
# Public dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AutoFailedTrace:
    """One trace whose in-flight Task(s) the scanner just compensated.

    ``affectedTaskIds`` is the ordered tuple of leaf Task IDs that crossed
    into a non-terminal state (`pending` / `pending_approval` / `dispatched`
    / `running`) without a corresponding terminal row. ``lastStates`` is the
    full reconstruction so callers (T083 ResultSummary builder) can render
    a per-leaf "interrupted at <state>" line if useful.
    """

    traceId: str
    userId: str | None
    eventId: str | None
    affectedTaskIds: tuple[str, ...]
    lastStates: Mapping[str, TaskState]


@dataclass
class ScannerStats:
    """Diagnostic counters exposed to operators / observability."""

    files_seen: int = 0
    rows_total: int = 0
    rows_skipped_malformed: int = 0
    in_flight_traces: int = 0
    in_flight_tasks: int = 0
    compensating_rows_written: int = 0


# --------------------------------------------------------------------------
# scan_audit_dir — read-only reconstruction.
# --------------------------------------------------------------------------


def _iter_audit_files(
    audit_dir: Path, *, since: datetime | None
) -> Iterator[Path]:
    """Yield ``audit-*.jsonl`` files in chronological (lexical) order."""
    if not audit_dir.exists():
        return
    files = sorted(audit_dir.glob("audit-*.jsonl"))
    for path in files:
        if since is not None:
            stem = path.stem  # 'audit-2026-04-24'
            try:
                day_str = stem.split("audit-", 1)[1]
                day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=UTC)
            except (IndexError, ValueError):
                # Unrecognised filename shape — yield it; let validate-step decide.
                yield path
                continue
            if day_dt < since:
                continue
        yield path


def _iter_events(
    audit_dir: Path,
    *,
    since: datetime | None,
    stats: ScannerStats,
) -> Iterator[AuditEvent]:
    for path in _iter_audit_files(audit_dir, since=since):
        stats.files_seen += 1
        try:
            handle = path.open("rb")
        except OSError as exc:  # pragma: no cover — disk errors are rare in tests
            sys.stderr.write(
                f"[audit-scanner] cannot open {path}: {exc!r}\n"
            )
            continue
        with handle as fp:
            for raw in fp:
                stats.rows_total += 1
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats.rows_skipped_malformed += 1
                    continue
                if not isinstance(obj, dict):
                    stats.rows_skipped_malformed += 1
                    continue
                try:
                    yield AuditEvent.model_validate(obj)
                except ValidationError:
                    stats.rows_skipped_malformed += 1
                    continue


def scan_audit_dir(
    audit_dir: Path,
    *,
    since_days: int = 7,
    stats: ScannerStats | None = None,
) -> list[AutoFailedTrace]:
    """Return every trace whose leaf Task(s) crashed in a non-terminal state.

    Args:
        audit_dir: directory containing ``audit-YYYY-MM-DD.jsonl`` files.
        since_days: ignore files older than this (FR-021 default 30 d log
            retention; restart-recovery only cares about *recent* runs).
            Pass a large value to scan everything.
        stats: optional ``ScannerStats`` to populate; useful for tests and
            for the kernel boot log.

    Returns:
        A list of :class:`AutoFailedTrace`. Empty if every Task is terminal.
    """
    audit_dir = Path(audit_dir)
    counters = stats if stats is not None else ScannerStats()

    since: datetime | None = None
    if since_days > 0:
        since = datetime.now(tz=UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - _days(since_days)

    last_state: dict[str, TaskState] = {}
    task_to_trace: dict[str, str] = {}
    user_id_by_trace: dict[str, str] = {}
    event_id_by_trace: dict[str, str] = {}
    trace_seen_order: list[str] = []
    leaf_seen_order: dict[str, list[str]] = {}

    for ev in _iter_events(audit_dir, since=since, stats=counters):
        if ev.eventType == "event_received" and ev.traceId:
            actor = ev.actor or ""
            if actor.startswith("user:"):
                user_id_by_trace.setdefault(ev.traceId, actor.split(":", 1)[1])
            extra = ev.extra or {}
            evt_id = extra.get("eventId")
            if isinstance(evt_id, str):
                event_id_by_trace.setdefault(ev.traceId, evt_id)
            if ev.traceId not in trace_seen_order:
                trace_seen_order.append(ev.traceId)
            continue

        new_state = _EVENT_TO_STATE.get(ev.eventType)
        if new_state is None or not ev.taskId or not ev.traceId:
            continue
        last_state[ev.taskId] = new_state
        task_to_trace[ev.taskId] = ev.traceId
        if ev.traceId not in trace_seen_order:
            trace_seen_order.append(ev.traceId)
        bucket = leaf_seen_order.setdefault(ev.traceId, [])
        if ev.taskId not in bucket:
            bucket.append(ev.taskId)

    affected: dict[str, list[str]] = {}
    for task_id, state in last_state.items():
        if state in TERMINAL_STATES:
            continue
        trace_id = task_to_trace[task_id]
        affected.setdefault(trace_id, []).append(task_id)

    results: list[AutoFailedTrace] = []
    for trace_id in trace_seen_order:
        task_ids = affected.get(trace_id)
        if not task_ids:
            continue
        results.append(
            AutoFailedTrace(
                traceId=trace_id,
                userId=user_id_by_trace.get(trace_id),
                eventId=event_id_by_trace.get(trace_id),
                affectedTaskIds=tuple(
                    tid for tid in leaf_seen_order.get(trace_id, []) if tid in task_ids
                ),
                lastStates={tid: last_state[tid] for tid in task_ids},
            )
        )

    counters.in_flight_traces = len(results)
    counters.in_flight_tasks = sum(len(r.affectedTaskIds) for r in results)
    return results


# --------------------------------------------------------------------------
# scan_and_autofail — read + write compensating rows.
# --------------------------------------------------------------------------


def scan_and_autofail(
    audit_dir: Path,
    *,
    writer: AuditWriter,
    clock: Callable[[], datetime] | None = None,
    actor: str = "kernel",
    since_days: int = 7,
    stats: ScannerStats | None = None,
) -> list[AutoFailedTrace]:
    """Scan + compensate. Return the list of traces that need ResultSummary push.

    Caller (T085 startup wiring) is responsible for routing the returned
    traces through the T083 ResultSummary builder + T084 retry-deliverer
    *before* opening the entry pipeline.
    """
    audit_dir = Path(audit_dir)
    counters = stats if stats is not None else ScannerStats()

    affected = scan_audit_dir(audit_dir, since_days=since_days, stats=counters)
    if not affected:
        return []

    now_fn: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    boot_marker = AuditEvent.model_validate(
        {
            "auditId": _new_audit_id(),
            "timestamp": now_fn(),
            "actor": actor,
            "eventType": "kernel_restart_detected",
            "extra": {
                "in_flight_traces": len(affected),
                "in_flight_tasks": counters.in_flight_tasks,
            },
        }
    )
    writer.write(boot_marker)
    counters.compensating_rows_written += 1

    for record in affected:
        trace_marker = AuditEvent.model_validate(
            {
                "auditId": _new_audit_id(),
                "timestamp": now_fn(),
                "actor": actor,
                "eventType": "in_flight_auto_failed",
                "traceId": record.traceId,
                "extra": {
                    "affectedTaskIds": list(record.affectedTaskIds),
                    "userId": record.userId,
                    "eventId": record.eventId,
                },
            }
        )
        writer.write(trace_marker)
        counters.compensating_rows_written += 1

        for task_id in record.affectedTaskIds:
            failed = AuditEvent.model_validate(
                {
                    "auditId": _new_audit_id(),
                    "timestamp": now_fn(),
                    "actor": actor,
                    "eventType": "task_failed",
                    "traceId": record.traceId,
                    "taskId": task_id,
                    "outcome": "failed",
                    "extra": {
                        "failureReason": "kernel_restart",
                        "previousState": record.lastStates[task_id],
                    },
                }
            )
            writer.write(failed)
            counters.compensating_rows_written += 1

    return affected


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _new_audit_id() -> str:
    """Fresh ULID-26 ID for a synthetic compensating event."""
    return str(ulid.new().str)


def _days(n: int) -> Any:
    """Local helper avoiding a top-level ``timedelta`` import collision."""
    from datetime import timedelta

    return timedelta(days=n)
