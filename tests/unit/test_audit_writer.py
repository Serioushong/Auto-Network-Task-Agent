"""T030 — failing unit test for AuditWriter.

RED until T031 ships `orchestrator_kernel.audit.writer.AuditWriter`.

Core contract:
- JSONL append (one JSON object per line, UTF-8, LF-terminated).
- UTC day-boundary rotation: a new file per UTC calendar day.
- On disk-write failure, AuditWriter MUST:
    * emit a `disk_write_failed` self-audit (best-effort) onto a fallback path
      OR stderr, and
    * flip `healthy` to False so the kernel's entry pipeline can start
      rejecting new events with `event_rejected_warming_up`.
- Dependency-inject the clock and the file-opener so tests drive both the
  rotation path and the failure path deterministically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestrator_kernel.audit.writer import AuditWriter, OpenerProtocol
from orchestrator_kernel.contracts.audit import AuditEvent


def _event(eid: str = "01J9AUDIT000000000", **overrides: Any) -> AuditEvent:
    base: dict[str, Any] = {
        "auditId": eid,
        "timestamp": datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC),
        "actor": "kernel",
        "eventType": "task_created",
        "traceId": "01J9TRACE000000000",
        "taskId": "01J9LEAF0000000000",
    }
    base.update(overrides)
    return AuditEvent.model_validate(base)


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class TestJsonlAppend:
    def test_one_line_per_event(self, tmp_audit_dir: Path) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC))
        w = AuditWriter(tmp_audit_dir, clock=clock.now)

        w.write(_event("01J9AUDIT0000000A1"))
        w.write(_event("01J9AUDIT0000000A2"))

        files = sorted(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        payloads = [json.loads(line) for line in lines]
        assert payloads[0]["auditId"] == "01J9AUDIT0000000A1"
        assert payloads[1]["auditId"] == "01J9AUDIT0000000A2"

    def test_file_name_contains_utc_date(self, tmp_audit_dir: Path) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 23, 59, 0, tzinfo=UTC))
        w = AuditWriter(tmp_audit_dir, clock=clock.now)
        w.write(_event())
        files = list(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 1
        assert "2026-04-21" in files[0].name


class TestUtcDayRotation:
    def test_rotates_across_midnight_utc(self, tmp_audit_dir: Path) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 23, 59, 0, tzinfo=UTC))
        w = AuditWriter(tmp_audit_dir, clock=clock.now)
        w.write(_event("01J9AUDIT0000000B1"))
        clock.advance(timedelta(minutes=2))  # cross midnight
        w.write(_event("01J9AUDIT0000000B2"))

        files = sorted(tmp_audit_dir.glob("*.jsonl"))
        assert len(files) == 2
        names = " ".join(p.name for p in files)
        assert "2026-04-21" in names
        assert "2026-04-22" in names


class TestDiskFailureFlipsUnhealthy:
    def test_write_failure_sets_unhealthy_and_self_audits(
        self, tmp_audit_dir: Path
    ) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC))

        class _FailingOpener:
            def __init__(self) -> None:
                self.calls = 0
                self.fallback_writes: list[str] = []

            def open_append(self, path: Path) -> Any:
                self.calls += 1
                raise OSError("simulated ENOSPC")

            def emergency_write(self, line: str) -> None:
                self.fallback_writes.append(line)

        opener: OpenerProtocol = _FailingOpener()  # type: ignore[assignment]
        w = AuditWriter(tmp_audit_dir, clock=clock.now, opener=opener)
        assert w.healthy is True

        w.write(_event("01J9AUDIT0000000C1"))

        assert w.healthy is False
        self_audits = getattr(opener, "fallback_writes", [])
        # At least one of the emergency writes is the disk_write_failed self-audit.
        found = False
        for line in self_audits:
            parsed = json.loads(line)
            if parsed.get("eventType") == "disk_write_failed":
                found = True
                assert parsed["actor"] == "system"
                break
        assert found, f"expected disk_write_failed self-audit; got {self_audits}"

    def test_unhealthy_rejects_subsequent_writes(self, tmp_audit_dir: Path) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC))

        class _FailingOpener:
            def open_append(self, path: Path) -> Any:
                raise OSError("disk full")

            def emergency_write(self, line: str) -> None:
                pass

        opener: OpenerProtocol = _FailingOpener()  # type: ignore[assignment]
        w = AuditWriter(tmp_audit_dir, clock=clock.now, opener=opener)
        w.write(_event("01J9AUDIT0000000D1"))
        # Second write after going unhealthy: MUST NOT raise (non-blocking),
        # but also MUST NOT attempt to re-open the bad path for every event.
        w.write(_event("01J9AUDIT0000000D2"))
        assert w.healthy is False


class TestSchemaValidationAtBoundary:
    def test_rejects_non_audit_event(self, tmp_audit_dir: Path) -> None:
        clock = FrozenClock(datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC))
        w = AuditWriter(tmp_audit_dir, clock=clock.now)
        with pytest.raises(TypeError):
            w.write({"not": "an AuditEvent"})  # type: ignore[arg-type]
