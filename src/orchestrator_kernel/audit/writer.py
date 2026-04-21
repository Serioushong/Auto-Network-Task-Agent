"""T031 — Append-only AuditWriter with UTC day rotation and disk-failure fallback.

Design goals:
- Single-source-of-truth JSONL audit log (FR-006 / FR-028): exactly one line
  per `AuditEvent`, UTF-8, LF-terminated.
- UTC day boundary rotation (FR-019 simplification): the file name embeds the
  current UTC date (YYYY-MM-DD); at the first write after midnight UTC, a
  fresh file is created automatically. No in-process time-based scheduler.
- Disk-failure containment (FR-021): on the first `OSError` from the opener,
  the writer flips `healthy=False`, emits a best-effort `disk_write_failed`
  self-audit via the injected emergency channel, and from that point on
  silently no-ops instead of crashing the kernel. The kernel's entrypoint
  reads `writer.healthy` and, when False, rejects new events with
  `event_rejected_warming_up` until operator intervention.
- Dependency injection of (a) the clock and (b) the file opener makes the
  rotation and failure paths unit-testable without flakiness.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..contracts.audit import AuditEvent


class OpenerProtocol(Protocol):
    """File-open + emergency-fallback injectable for tests + ops edge cases.

    In production `DefaultOpener` uses native `Path.open("ab")`. Tests inject
    a mock that raises OSError to drive the unhealthy path.
    """

    def open_append(self, path: Path) -> Any:  # returns a writable binary file-like
        ...

    def emergency_write(self, line: str) -> None: ...


class DefaultOpener:
    """Opens files for binary-append writes; emergency path goes to stderr."""

    def open_append(self, path: Path) -> Any:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab")

    def emergency_write(self, line: str) -> None:
        # Best-effort. If stderr is also broken we simply give up — by design.
        try:
            sys.stderr.write(line if line.endswith("\n") else line + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 — last-resort path, broad catch is intentional
            pass


class AuditWriter:
    """Append-only JSONL audit log with UTC-day rotation and fail-open recovery."""

    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        opener: OpenerProtocol | None = None,
    ) -> None:
        self._dir = Path(directory)
        self._clock: Callable[[], datetime] = clock or (
            lambda: datetime.now(tz=UTC)
        )
        self._opener: OpenerProtocol = opener or DefaultOpener()
        self._healthy: bool = True

    @property
    def healthy(self) -> bool:
        """False after the first disk-write failure; never flips back to True."""
        return self._healthy

    def write(self, event: AuditEvent) -> None:
        """Append one AuditEvent as JSONL. No-ops silently once unhealthy."""
        if not isinstance(event, AuditEvent):
            raise TypeError(
                f"AuditWriter.write requires AuditEvent; got {type(event).__name__}"
            )
        if not self._healthy:
            return

        line = event.model_dump_json(exclude_none=True)
        try:
            path = self._current_path()
            with self._opener.open_append(path) as fp:
                fp.write((line + "\n").encode("utf-8"))
        except OSError as exc:
            self._transition_unhealthy(exc)

    def _current_path(self) -> Path:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now_utc = now.astimezone(UTC)
        return self._dir / f"audit-{now_utc.strftime('%Y-%m-%d')}.jsonl"

    def _transition_unhealthy(self, cause: OSError) -> None:
        self._healthy = False
        self_audit = {
            "auditId": "system-disk-write-failed",
            "timestamp": self._clock_iso(),
            "actor": "system",
            "eventType": "disk_write_failed",
            "extra": {"error": str(cause), "error_type": type(cause).__name__},
        }
        try:
            self._opener.emergency_write(json.dumps(self_audit, ensure_ascii=False))
        except Exception:  # noqa: BLE001 — emergency path MUST NOT re-raise
            pass

    def _clock_iso(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(UTC).isoformat()
