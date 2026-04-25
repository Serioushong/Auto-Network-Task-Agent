"""T081 — RED unit tests for `orchestrator_kernel.audit.scanner`.

Scope (Phase 8 / US6):

The scanner is the kernel's crash-recovery primitive: on startup it walks the
on-disk audit JSONL files (the *single source of truth*, FR-006 / FR-019),
reconstructs every Task's last observed state, and for every Task that never
reached a terminal state it MUST emit a compensating
``task_failed(reason=kernel_restart)`` event. The list of affected traces is
returned so the caller (T085 startup wiring) can push a `ResultSummary` per
trace before opening the entry pipeline.

Invariants under test:
  * **INV-5** — the `(traceId, auditId)` sequence allows independent
    reconstruction of a Trace's state machine. Property test exercises this
    via random state sequences.
  * **INV-6** — every non-terminal Task at the time of crash MUST appear in
    the next kernel boot's `failed(kernel_restart)` set.

Module contract (RED placeholder; T082 will implement):

    @dataclass
    class AutoFailedTrace:
        traceId: str
        userId: str | None
        eventId: str | None
        affectedTaskIds: tuple[str, ...]
        lastStates: Mapping[str, TaskState]

    def scan_audit_dir(audit_dir: Path, *, since_days: int = 7
                      ) -> list[AutoFailedTrace]: ...

    def scan_and_autofail(
        audit_dir: Path,
        *,
        writer: AuditWriter,
        clock: Callable[[], datetime] | None = None,
        actor: str = "kernel",
        since_days: int = 7,
    ) -> list[AutoFailedTrace]: ...

These tests are RED until ``scanner.py`` exists with the contract above.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orchestrator_kernel.audit.writer import AuditWriter
from orchestrator_kernel.contracts.audit import AuditEvent, AuditEventType

# --------------------------------------------------------------------------
# Imports under test (RED — module does not exist yet, T082 will add it).
# --------------------------------------------------------------------------
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

scanner = pytest.importorskip(
    "orchestrator_kernel.audit.scanner",
    reason="T082 has not landed audit/scanner.py yet (this test is RED).",
)


# --------------------------------------------------------------------------
# Helpers — synthesise audit JSONL files matching the real writer's layout.
# --------------------------------------------------------------------------


def _audit_id(prefix: str, n: int) -> str:
    """Make a 16+ char auditId that satisfies the contract pattern."""
    base = f"{prefix}{n:08d}"
    return base.ljust(20, "0")[:24]


def _make_event(
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
    payload: dict[str, Any] = {
        "auditId": audit_id,
        "timestamp": timestamp,
        "actor": actor,
        "eventType": event_type,
    }
    if trace_id is not None:
        payload["traceId"] = trace_id
    if task_id is not None:
        payload["taskId"] = task_id
    if extra is not None:
        payload["extra"] = extra
    if outcome is not None:
        payload["outcome"] = outcome
    return AuditEvent.model_validate(payload)


def _write_jsonl(directory: Path, day: datetime, events: list[AuditEvent]) -> Path:
    """Bypass AuditWriter and craft a file the way the writer would on `day`."""
    directory.mkdir(parents=True, exist_ok=True)
    fname = f"audit-{day.astimezone(UTC).strftime('%Y-%m-%d')}.jsonl"
    path = directory / fname
    with path.open("ab") as fp:
        for ev in events:
            fp.write((ev.model_dump_json(exclude_none=True) + "\n").encode("utf-8"))
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def t0() -> datetime:
    return datetime(2026, 4, 24, 10, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# Core scenario tests
# --------------------------------------------------------------------------


class TestScanAuditDir:
    def test_in_flight_dispatched_task_is_returned(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        trace_id = "01J9TRACE000000A001"
        task_id = "01J9LEAF0000000A001"
        events = [
            _make_event(
                audit_id=_audit_id("E", 1),
                event_type="event_received",
                timestamp=t0,
                trace_id=trace_id,
                actor="user:alice",
                extra={"eventId": "01J9EVENT0000000A01"},
            ),
            _make_event(
                audit_id=_audit_id("T", 1),
                event_type="task_created",
                timestamp=t0 + timedelta(seconds=1),
                trace_id=trace_id,
                task_id=task_id,
            ),
            _make_event(
                audit_id=_audit_id("D", 1),
                event_type="task_dispatched",
                timestamp=t0 + timedelta(seconds=2),
                trace_id=trace_id,
                task_id=task_id,
            ),
            # ❌ no terminal event — kernel crashed here.
        ]
        _write_jsonl(tmp_audit_dir, t0, events)

        in_flight = scanner.scan_audit_dir(tmp_audit_dir)

        assert len(in_flight) == 1
        affected = in_flight[0]
        assert affected.traceId == trace_id
        assert task_id in tuple(affected.affectedTaskIds)
        assert affected.lastStates[task_id] in {
            "dispatched",
            "running",
            "pending",
            "pending_approval",
        }

    def test_terminal_task_is_not_returned(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        trace_id = "01J9TRACE000000B001"
        task_id = "01J9LEAF0000000B001"
        events = [
            _make_event(
                audit_id=_audit_id("E", 2),
                event_type="event_received",
                timestamp=t0,
                trace_id=trace_id,
                actor="user:bob",
            ),
            _make_event(
                audit_id=_audit_id("T", 2),
                event_type="task_created",
                timestamp=t0 + timedelta(seconds=1),
                trace_id=trace_id,
                task_id=task_id,
            ),
            _make_event(
                audit_id=_audit_id("D", 2),
                event_type="task_dispatched",
                timestamp=t0 + timedelta(seconds=2),
                trace_id=trace_id,
                task_id=task_id,
            ),
            _make_event(
                audit_id=_audit_id("S", 2),
                event_type="task_succeeded",
                timestamp=t0 + timedelta(seconds=3),
                trace_id=trace_id,
                task_id=task_id,
                outcome="succeeded",
            ),
        ]
        _write_jsonl(tmp_audit_dir, t0, events)

        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert in_flight == []

    def test_mixed_returns_only_in_flight(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # Two traces: one finished, one in-flight. Scanner must flag only one.
        finished_trace = "01J9TRACE000000C001"
        finished_task = "01J9LEAF0000000C001"
        live_trace = "01J9TRACE000000C002"
        live_task = "01J9LEAF0000000C002"
        events = [
            _make_event(
                audit_id=_audit_id("F", 1),
                event_type="task_created",
                timestamp=t0,
                trace_id=finished_trace,
                task_id=finished_task,
            ),
            _make_event(
                audit_id=_audit_id("F", 2),
                event_type="task_succeeded",
                timestamp=t0 + timedelta(seconds=1),
                trace_id=finished_trace,
                task_id=finished_task,
                outcome="succeeded",
            ),
            _make_event(
                audit_id=_audit_id("L", 1),
                event_type="task_created",
                timestamp=t0 + timedelta(seconds=2),
                trace_id=live_trace,
                task_id=live_task,
            ),
            _make_event(
                audit_id=_audit_id("L", 2),
                event_type="task_dispatched",
                timestamp=t0 + timedelta(seconds=3),
                trace_id=live_trace,
                task_id=live_task,
            ),
        ]
        _write_jsonl(tmp_audit_dir, t0, events)

        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert len(in_flight) == 1
        assert in_flight[0].traceId == live_trace
        assert tuple(in_flight[0].affectedTaskIds) == (live_task,)

    def test_walks_multiple_jsonl_files_in_order(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # Day-1 file: dispatch.  Day-2 file: succeed.  Net: terminal, NOT in-flight.
        trace_id = "01J9TRACE000000D001"
        task_id = "01J9LEAF0000000D001"
        day_1 = t0
        day_2 = t0 + timedelta(days=1)
        _write_jsonl(
            tmp_audit_dir,
            day_1,
            [
                _make_event(
                    audit_id=_audit_id("D", 10),
                    event_type="task_created",
                    timestamp=day_1,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("D", 11),
                    event_type="task_dispatched",
                    timestamp=day_1 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            ],
        )
        _write_jsonl(
            tmp_audit_dir,
            day_2,
            [
                _make_event(
                    audit_id=_audit_id("D", 12),
                    event_type="task_succeeded",
                    timestamp=day_2,
                    trace_id=trace_id,
                    task_id=task_id,
                    outcome="succeeded",
                ),
            ],
        )

        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert in_flight == []

    def test_pending_approval_is_in_flight(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # Spec FR-028 explicitly lists pending_approval as in-flight.
        trace_id = "01J9TRACE000000E001"
        task_id = "01J9LEAF0000000E001"
        _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("P", 1),
                    event_type="task_created",
                    timestamp=t0,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("P", 2),
                    event_type="task_pending_approval",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            ],
        )
        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert len(in_flight) == 1
        assert in_flight[0].lastStates[task_id] == "pending_approval"

    def test_malformed_lines_are_skipped_gracefully(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # Write one valid + one garbage line.
        path = _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("OK", 1),
                    event_type="task_created",
                    timestamp=t0,
                    trace_id="01J9TRACE000000F001",
                    task_id="01J9LEAF0000000F001",
                ),
                _make_event(
                    audit_id=_audit_id("OK", 2),
                    event_type="task_succeeded",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id="01J9TRACE000000F001",
                    task_id="01J9LEAF0000000F001",
                    outcome="succeeded",
                ),
            ],
        )
        with path.open("ab") as fp:
            fp.write(b"this-is-not-json\n")
            fp.write(b'{"missing":"required-fields"}\n')

        # MUST NOT raise, even with garbage in the file.
        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert in_flight == []

    def test_idempotent_replay_events_are_ignored(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # Idempotent replay events MUST NOT mark a task as freshly in-flight;
        # the canonical state line is the authoritative one.
        trace_id = "01J9TRACE000000G001"
        task_id = "01J9LEAF0000000G001"
        _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("R", 1),
                    event_type="task_created",
                    timestamp=t0,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("R", 2),
                    event_type="task_succeeded",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                    outcome="succeeded",
                ),
                _make_event(
                    audit_id=_audit_id("R", 3),
                    event_type="idempotent_replay",
                    timestamp=t0 + timedelta(seconds=2),
                    trace_id=trace_id,
                ),
            ],
        )
        in_flight = scanner.scan_audit_dir(tmp_audit_dir)
        assert in_flight == []


# --------------------------------------------------------------------------
# scan_and_autofail — writes compensating events through AuditWriter.
# --------------------------------------------------------------------------


class TestScanAndAutofail:
    def test_writes_failed_kernel_restart_event_per_in_flight_task(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        trace_id = "01J9TRACE000000H001"
        task_id = "01J9LEAF0000000H001"
        path = _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("H", 1),
                    event_type="task_created",
                    timestamp=t0,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("H", 2),
                    event_type="task_dispatched",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            ],
        )
        # Use a clock pinned to the same UTC day so the writer appends to the
        # same file we crafted (assertable round-trip).
        boot_time = t0 + timedelta(seconds=10)
        writer = AuditWriter(tmp_audit_dir, clock=lambda: boot_time)

        affected = scanner.scan_and_autofail(
            tmp_audit_dir, writer=writer, clock=lambda: boot_time
        )

        assert len(affected) == 1
        # Re-read the file and verify two compensating events were appended.
        rows = _read_jsonl(path)
        appended_types = [r["eventType"] for r in rows[2:]]
        assert "kernel_restart_detected" in appended_types
        assert "in_flight_auto_failed" in appended_types
        assert "task_failed" in appended_types
        # The failed event MUST carry failureReason=kernel_restart.
        failed_rows = [r for r in rows if r["eventType"] == "task_failed"]
        assert failed_rows, "expected at least one synthetic task_failed row"
        f = failed_rows[-1]
        assert f["taskId"] == task_id
        assert f["traceId"] == trace_id
        assert f.get("outcome") == "failed"
        assert (f.get("extra") or {}).get("failureReason") == "kernel_restart"

    def test_idempotent_when_run_twice(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        # After scan_and_autofail flips the task to failed(kernel_restart),
        # a second run on the same dir MUST find zero in-flight tasks and
        # MUST NOT append additional rows.
        trace_id = "01J9TRACE000000I001"
        task_id = "01J9LEAF0000000I001"
        path = _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("I", 1),
                    event_type="task_created",
                    timestamp=t0,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("I", 2),
                    event_type="task_dispatched",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            ],
        )
        boot_time = t0 + timedelta(seconds=10)
        writer = AuditWriter(tmp_audit_dir, clock=lambda: boot_time)

        first = scanner.scan_and_autofail(
            tmp_audit_dir, writer=writer, clock=lambda: boot_time
        )
        rows_after_first = len(_read_jsonl(path))
        assert len(first) == 1

        second = scanner.scan_and_autofail(
            tmp_audit_dir, writer=writer, clock=lambda: boot_time
        )
        rows_after_second = len(_read_jsonl(path))

        assert second == []
        assert rows_after_second == rows_after_first, (
            "second run must be a no-op (idempotent)"
        )

    def test_returns_userid_and_eventid_when_present(
        self, tmp_audit_dir: Path, t0: datetime
    ) -> None:
        trace_id = "01J9TRACE000000J001"
        task_id = "01J9LEAF0000000J001"
        _write_jsonl(
            tmp_audit_dir,
            t0,
            [
                _make_event(
                    audit_id=_audit_id("J", 1),
                    event_type="event_received",
                    timestamp=t0,
                    trace_id=trace_id,
                    actor="user:carol",
                    extra={"eventId": "01J9EVENT0000000J01"},
                ),
                _make_event(
                    audit_id=_audit_id("J", 2),
                    event_type="task_created",
                    timestamp=t0 + timedelta(seconds=1),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
                _make_event(
                    audit_id=_audit_id("J", 3),
                    event_type="task_dispatched",
                    timestamp=t0 + timedelta(seconds=2),
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            ],
        )
        boot_time = t0 + timedelta(seconds=10)
        writer = AuditWriter(tmp_audit_dir, clock=lambda: boot_time)

        affected = scanner.scan_and_autofail(
            tmp_audit_dir, writer=writer, clock=lambda: boot_time
        )

        assert len(affected) == 1
        record = affected[0]
        assert record.userId == "carol"
        assert record.eventId == "01J9EVENT0000000J01"


# --------------------------------------------------------------------------
# Property tests for INV-5 / INV-6.
# --------------------------------------------------------------------------

NON_TERMINAL_EVENTS: tuple[AuditEventType, ...] = (
    "task_created",
    "task_pending_approval",
    "task_dispatched",
    "task_started",
)
TERMINAL_EVENTS: tuple[AuditEventType, ...] = (
    "task_succeeded",
    "task_failed",
    "task_cancelled",
)
TERMINAL_OUTCOMES: dict[AuditEventType, str] = {
    "task_succeeded": "succeeded",
    "task_failed": "failed",
    "task_cancelled": "cancelled",
}


@st.composite
def _task_history(draw: st.DrawFn) -> tuple[str, list[AuditEventType], bool]:
    """Generate (taskId, ordered_event_types, did_terminate)."""
    suffix = draw(
        st.text(
            alphabet="0123456789ABCDEF", min_size=8, max_size=8
        )
    )
    task_id = f"01J9LEAFPROP{suffix}"
    n_non_term = draw(st.integers(min_value=1, max_value=4))
    seq: list[AuditEventType] = list(
        draw(
            st.lists(
                st.sampled_from(NON_TERMINAL_EVENTS),
                min_size=n_non_term,
                max_size=n_non_term,
            )
        )
    )
    did_terminate = draw(st.booleans())
    if did_terminate:
        seq.append(draw(st.sampled_from(TERMINAL_EVENTS)))
    return task_id, seq, did_terminate


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(histories=st.lists(_task_history(), min_size=1, max_size=6, unique_by=lambda x: x[0]))
def test_inv6_every_non_terminal_task_is_caught(
    tmp_audit_dir: Path, t0: datetime, histories: list[tuple[str, list[AuditEventType], bool]]
) -> None:
    """INV-6: any task without a terminal event MUST be in scanner output."""
    audit_dir = tmp_audit_dir / "inv6"
    audit_dir.mkdir(parents=True, exist_ok=True)
    events: list[AuditEvent] = []
    seq_no = 0
    expected_in_flight: set[str] = set()
    for idx, (task_id, types, did_terminate) in enumerate(histories):
        trace_id = f"01J9TRACEPROP{idx:08d}".ljust(20, "0")[:24]
        for et in types:
            seq_no += 1
            events.append(
                _make_event(
                    audit_id=_audit_id("X", seq_no),
                    event_type=et,
                    timestamp=t0 + timedelta(seconds=seq_no),
                    trace_id=trace_id,
                    task_id=task_id,
                    outcome=TERMINAL_OUTCOMES.get(et),
                )
            )
        if not did_terminate:
            expected_in_flight.add(task_id)
    _write_jsonl(audit_dir, t0, events)

    in_flight = scanner.scan_audit_dir(audit_dir)
    actual_task_ids: set[str] = set()
    for record in in_flight:
        actual_task_ids.update(record.affectedTaskIds)
    # INV-6: every non-terminal task is caught.  Scanner MAY also flag
    # something terminal-looking-but-malformed, but for cleanly-shaped
    # input the sets must be equal.
    assert expected_in_flight.issubset(actual_task_ids)
    # No terminal task is mistakenly reported as in-flight.
    terminated = {
        task_id for task_id, _types, did in histories if did
    }
    assert terminated.isdisjoint(actual_task_ids)


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(histories=st.lists(_task_history(), min_size=1, max_size=4, unique_by=lambda x: x[0]))
def test_inv5_replay_is_deterministic(
    tmp_audit_dir: Path, t0: datetime, histories: list[tuple[str, list[AuditEventType], bool]]
) -> None:
    """INV-5: rerunning scan_audit_dir on the same JSONL yields the same set.

    This is the 'roundtrip' property — given identical inputs, the scanner
    derives identical (taskId, lastState) reconstructions across calls.
    """
    audit_dir = tmp_audit_dir / "inv5"
    audit_dir.mkdir(parents=True, exist_ok=True)
    events: list[AuditEvent] = []
    seq_no = 0
    for idx, (task_id, types, _did_terminate) in enumerate(histories):
        trace_id = f"01J9TRACEDET{idx:08d}".ljust(20, "0")[:24]
        for et in types:
            seq_no += 1
            events.append(
                _make_event(
                    audit_id=_audit_id("Y", seq_no),
                    event_type=et,
                    timestamp=t0 + timedelta(seconds=seq_no),
                    trace_id=trace_id,
                    task_id=task_id,
                    outcome=TERMINAL_OUTCOMES.get(et),
                )
            )
    _write_jsonl(audit_dir, t0, events)

    first = scanner.scan_audit_dir(audit_dir)
    second = scanner.scan_audit_dir(audit_dir)

    def _key(records: list[Any]) -> list[tuple[str, tuple[str, ...]]]:
        return sorted(
            (r.traceId, tuple(sorted(r.affectedTaskIds))) for r in records
        )

    assert _key(first) == _key(second)
