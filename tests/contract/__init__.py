"""Contract-test subpackage.

Unlike `tests/integration/` and `tests/unit/` (kept as namespace directories
per Phase 1.5 T006e), this directory ships a shared helper module
(`_common.py`) that is consumed via `from ._common import ...`. Relative
imports require a real package, so this subdir keeps its `__init__.py`.

The layout asymmetry is intentional — see `tests/conftest.py` Layout note.
"""
