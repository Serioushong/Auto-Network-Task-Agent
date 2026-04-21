"""Scaffold smoke test — proves the T001–T005 setup wires together.

⚠️ TRANSITIONAL FILE — delete (or convert) after T007 lands.

This file ONLY exists so Phase 1 Setup (T006) ends with pytest exit code 0
instead of 5 (no-tests-collected). It tests the *scaffold structure*, not any
behaviour, so it does not violate Constitution Article VIII's
"Red → Green → Refactor" — there is no behaviour to drive yet.

Lifecycle:
  - Phase 1 (now): file is GREEN; gates `uv run pytest -q`.
  - Phase 2 (T007+): once the first contract test (`tests/contract/test_entry_event.py`)
    is collected, this file becomes redundant and SHOULD be removed in the same
    commit (or kept only if it ever fails to import — i.e., as a structural canary).

Invariants asserted (intentionally trivial):
  1. The `orchestrator_kernel` package is importable from the configured `pythonpath`.
  2. The package declares a semver-looking `__version__`.
  3. All seven declared subpackages are present (matches plan.md §Project Structure).
  4. The `workers_stub` package is importable.
"""

from __future__ import annotations

import importlib


def test_orchestrator_kernel_package_importable() -> None:
    pkg = importlib.import_module("orchestrator_kernel")
    assert hasattr(pkg, "__version__")
    version = pkg.__version__
    assert isinstance(version, str) and version.count(".") == 2


def test_all_subpackages_present() -> None:
    expected = [
        "orchestrator_kernel.contracts",
        "orchestrator_kernel.entrypoints",
        "orchestrator_kernel.kernel",
        "orchestrator_kernel.worker_supervisor",
        "orchestrator_kernel.audit",
        "orchestrator_kernel.notifier",
        "orchestrator_kernel.llm",
    ]
    for dotted in expected:
        importlib.import_module(dotted)


def test_workers_stub_package_importable() -> None:
    importlib.import_module("workers_stub")
