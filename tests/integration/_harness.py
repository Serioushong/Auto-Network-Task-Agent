"""Shared types for Phase 3+ integration tests.

Kept in a regular module (rather than conftest.py) so tests can do
`from ._harness import KernelHarnessProtocol, WorkerSpec, TraceResult`
without tripping pytest's conftest-is-not-importable rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkerSpec:
    """Description of one worker subprocess the harness should spawn."""

    script_path: Path
    expected_capabilities: tuple[str, ...]


@dataclass
class TraceResult:
    """Minimal DTO returned by `KernelHarness.submit()`.

    Fields mirror `ResultSummary` plus a raw-audit accessor so integration
    tests can assert the audit chain (FR-006 / FR-019).
    """

    traceId: str
    eventId: str
    traceOutcome: str
    leafOutcomes: list[str] = field(default_factory=list)
    audit_event_types: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    message: str = ""


class KernelHarnessProtocol:
    """Typing stub so tests can reference the expected surface.

    Implemented by whatever `assemble_kernel()` returns in T045. Kept here
    purely so tests typecheck before GREEN lands.
    """

    async def submit(
        self,
        *,
        text: str,
        user_id: str = "test-user",
        event_id: str | None = None,
        timeout_s: float = 5.0,
    ) -> TraceResult: ...

    async def register_worker(self, spec: WorkerSpec) -> None: ...

    async def shutdown(self) -> None: ...
