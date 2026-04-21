"""Integration-test subpackage.

Like `tests/contract/`, this directory ships a shared helper module
(`_harness.py`) consumed via `from ._harness import ...`. Relative imports
require a real package, so this subdir keeps `__init__.py`. See
`tests/conftest.py` Layout note.
"""
