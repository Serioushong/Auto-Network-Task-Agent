"""T099 — LLM client Protocol abstraction.

The orchestrator kernel keeps the planner behind an interface so the
implementation can swap from the current rule-based stub to a real vendor
SDK without touching intake, audit, or worker orchestration code.

MVP policy:
- No concrete provider implementation lives here.
- ``NotImplementedLLMClient`` exists only as a guard rail so accidental
  use fails loudly during development.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal planning contract for future LLM-backed orchestration."""

    async def plan(self, *, text: str) -> dict[str, object]:
        """Return a structured plan for the supplied text."""


class NotImplementedLLMClient:
    """Default placeholder that refuses all calls."""

    async def plan(self, *, text: str) -> dict[str, object]:
        raise NotImplementedError(
            "LLMClient is not implemented in the MVP; use the rule-based planner"
        )


__all__ = ["LLMClient", "NotImplementedLLMClient"]
