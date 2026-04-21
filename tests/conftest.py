"""Root test fixtures shared by contract / integration / unit suites.

Keep this file minimal; suite-specific fixtures belong next to the tests that
consume them (`tests/<suite>/conftest.py`). The `tmp_audit_dir` fixture is
defined here because multiple suites need a clean audit-log directory wired
from pytest's `tmp_path`.

Layout note (see tasks.md T006e / T006e+ / review finding M2):
  The `tests/` directory mostly uses namespace-package discovery — pytest
  finds test modules via rootdir + testpaths (see `pyproject.toml`
  `[tool.pytest.ini_options]`). This lets same-named `test_foo.py` coexist
  across `tests/unit/`, `tests/integration/`, `tests/contract/` without
  collection conflicts.

  Exception: `tests/contract/` ships a shared helper module `_common.py`
  consumed via `from ._common import ...`. Relative imports require a real
  package, so that subdirectory keeps its `__init__.py`. `tests/unit/` and
  `tests/integration/` remain namespace directories with no `__init__.py`.
  If you ever need to turn the other subdirs into importable packages, add
  `__init__.py` back AND rename overlapping test modules.
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
