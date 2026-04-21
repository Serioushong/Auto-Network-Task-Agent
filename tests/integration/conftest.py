"""Integration-test fixtures for Phase 3 User Story 1 (T036~T038 RED).

Design:
- `kernel_harness` is the **test-side facade** over the assembled kernel. In
  RED it attempts to import `assemble_kernel` from `cli_main`; that symbol
  lands in T045, so today the fixture fails with `ImportError` and every
  test depending on it is flagged RED cleanly.
- `echo_worker_script` / `*_worker_spec` are path fixtures resolving to the
  `src/workers_stub/` scripts; the script files land in T043 / T066, so in
  RED the roundtrip test raises `FileNotFoundError` via the explicit
  `exists()` assertion.
- Shared dataclasses live in `_harness.py` so tests can import them
  relatively (`from ._harness import ...`). See `tests/integration/
  __init__.py` for the package-layout rationale.

The assertions inside tests are phrased against the **eventual** harness
API so that once GREEN lands the tests start passing without rewrite.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from ._harness import KernelHarnessProtocol, WorkerSpec


@pytest_asyncio.fixture
async def kernel_harness(tmp_audit_dir: Path) -> AsyncIterator[KernelHarnessProtocol]:
    """Provide an assembled kernel wired against a temp audit dir.

    RED behaviour (T036~T038): `assemble_kernel` does not yet exist in
    `cli_main`, so this import fails with ImportError and every dependent
    test errors out. Once T045 lands the import resolves and tests
    transition GREEN.
    """
    from orchestrator_kernel.cli_main import assemble_kernel  # type: ignore[attr-defined]

    harness = await assemble_kernel(audit_dir=tmp_audit_dir)
    try:
        yield harness
    finally:
        await harness.shutdown()


@pytest.fixture
def echo_worker_script() -> Path:
    """Path to the echo-worker stub module.

    RED: the file does not exist yet (T043). The roundtrip test asserts
    `.exists()` before spawning, so failure is explicit rather than a
    cryptic subprocess error.
    """
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "workers_stub"
        / "echo_worker.py"
    )


@pytest.fixture
def echo_worker_spec(echo_worker_script: Path) -> WorkerSpec:
    return WorkerSpec(
        script_path=echo_worker_script,
        expected_capabilities=("echo.say",),
    )


def _collect_audit_events(audit_dir: Path) -> list[dict[str, Any]]:
    """Read every *.jsonl file under `audit_dir` in name order."""
    events: list[dict[str, Any]] = []
    for jf in sorted(audit_dir.glob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


@pytest.fixture
def audit_events_factory(tmp_audit_dir: Path) -> Callable[[], list[dict[str, Any]]]:
    """Expose `_collect_audit_events` as a test-time callable."""

    def _factory() -> list[dict[str, Any]]:
        return _collect_audit_events(tmp_audit_dir)

    return _factory
