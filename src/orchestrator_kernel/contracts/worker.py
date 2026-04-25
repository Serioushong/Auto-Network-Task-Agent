"""T019 — Worker registration pydantic mirror of `contracts/worker-registration.schema.json`.

Capability name pattern (`^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$`), risk level,
self-declared per-capability Budget (delegating hard-cap enforcement to
`Budget` type), and resource limits.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .budget import Budget

RiskLevel = Literal["NORMAL", "HIGH_RISK"]


class Capability(BaseModel):
    """One Worker-declared capability. Dot-separated lowercase name, 1+ segments after the first."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
        max_length=64,
    )
    riskLevel: RiskLevel
    budget: Budget
    description: str | None = Field(default=None, max_length=256)


class ResourceLimits(BaseModel):
    """OS-level sandbox bounds enforced by kernel via Windows Job Object (FR-025)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    memory_mb: int = Field(ge=16, le=8_192, alias="memoryMb")
    cpu_pct: int = Field(ge=1, le=400, alias="cpuPct")
    wall_clock_ms: int = Field(ge=1, le=1_800_000, alias="wallClockMs")


class WorkerRegistration(BaseModel):
    """Sent as the first stdio frame after Worker spawn. FR-007 / FR-018 / FR-025."""

    model_config = ConfigDict(extra="forbid")

    workerId: str = Field(min_length=1, max_length=128)
    pid: int = Field(ge=1)
    capabilities: list[Capability] = Field(min_length=1)
    resourceLimits: ResourceLimits
