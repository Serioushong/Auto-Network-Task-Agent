"""Phase 6 smoke follow-up — CLI ``submit`` user-facing error UX.

Issue captured during the Phase 6 manual smoke pass: passing a short
``--event-id`` (e.g. ``E-DEMO-1``, 9 chars, below the
``EntryEvent.eventId`` ``min_length=16`` contract) used to bubble up
the raw ``pydantic.ValidationError`` traceback directly to the
operator's terminal.

This module pins the fix:

* **clean exit code 2** — no python-tracebacks leaking;
* **single-line stderr message** — names the offending flag
  (``--event-id``) and the contract window (``16–64`` chars);
* **`--help` advertises the constraint** — operators can self-serve
  the right format without spelunking through pydantic errors.

Uses ``typer.testing.CliRunner`` so we exercise the real Typer app
exactly as the ``orchestrator-kernel`` console script would.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator_kernel.cli_main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_short_event_id_prints_friendly_error(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "submit",
            "--text",
            "echo hi",
            "--event-id",
            "E-DEMO-1",
            "--audit-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (result.stderr or "")
    assert "Traceback" not in combined, (
        "pydantic traceback MUST NOT leak to the operator's terminal"
    )
    assert "--event-id" in combined
    assert "16" in combined and "64" in combined


def test_submit_help_mentions_event_id_length(runner: CliRunner) -> None:
    result = runner.invoke(app, ["submit", "--help"])
    assert result.exit_code == 0
    text = (result.output or "") + (result.stderr or "")
    assert "16" in text and "64" in text, (
        "submit --help MUST advertise the ULID-26 length window so "
        "operators can self-serve correct values without reading "
        "pydantic tracebacks"
    )
