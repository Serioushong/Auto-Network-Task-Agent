"""Root test fixtures shared by contract / integration / unit suites.

Keep this file minimal; suite-specific fixtures belong next to the tests that
consume them (`tests/<suite>/conftest.py`). The `tmp_audit_dir` fixture is
defined here because multiple suites need a clean audit-log directory wired
from pytest's `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_audit_dir(tmp_path: Path) -> Path:
    """Yield a fresh, empty directory intended for JSONL audit-log writes.

    Tests MUST NOT assume any files pre-exist here; the AuditWriter will
    create its own rotating files under this path. Isolated per test via
    `tmp_path`, so parallel runs stay safe.
    """
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir
