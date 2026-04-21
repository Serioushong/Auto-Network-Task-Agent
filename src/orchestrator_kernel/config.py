"""T026 — Kernel runtime configuration loaded from `var/kernel.toml` (optional).

Uses pydantic-settings so defaults, TOML file, and environment variables stack
in a predictable order. Any deployment value that would exceed the contract
hard caps (from `contracts.budget.SYSTEM_HARD_CAP`) is rejected at startup,
implementing the "fail-fast on mis-config" stance required by FR-018 and the
Constitution's Safety-Gates principle.

The TOML file is OPTIONAL — if `var/kernel.toml` is missing, defaults apply.
This keeps local-dev / CI ergonomic without hand-authoring a config file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .contracts.budget import SYSTEM_HARD_CAP, Budget


class RateLimitDefaults(BaseModel):
    """Defaults for the four rate-limiting dimensions (FR-026 / FR-027).

    These match the numbers pinned in spec.md `速率限制与并发控制`.
    Per-capability overrides are resolved at dispatch time; this struct only
    carries the deploy-wide baseline.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    global_rps: int = Field(default=50, ge=1, le=10_000)
    user_rpm: int = Field(default=120, ge=1, le=100_000)
    user_concurrent: int = Field(default=10, ge=1, le=10_000)
    user_highrisk_concurrent: int = Field(default=1, ge=1, le=100)


class KernelConfig(BaseSettings):
    """Top-level kernel config. Instantiate with `KernelConfig()` at startup.

    Resolution precedence (highest first):
      1. Constructor kwargs (tests)
      2. `var/kernel.toml` (deploy-time overrides)
      3. OS environment (prefix `KERNEL_`)
      4. Hard-coded defaults (this class)
    """

    model_config = SettingsConfigDict(
        toml_file="var/kernel.toml",
        env_prefix="KERNEL_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    CONTRACT_HARD_CAP: ClassVar[Budget] = SYSTEM_HARD_CAP
    """Read-only mirror of the wire contract's hard cap. Configs exceeding this fail."""

    system_hard_cap: Budget = SYSTEM_HARD_CAP
    approval_timeout_default_ms: int = Field(default=600_000, ge=1_000, le=3_600_000)
    """FR-011 / Clarification Q1: 10 minutes. Overridable 1s..1h at deploy time."""

    payload_max_bytes_default: int = Field(default=16_384, ge=1, le=1_048_576)
    """FR-031 / Clarification Q5: 16 KB text body cap. Overridable up to 1 MB."""

    rate_limit_defaults: RateLimitDefaults = Field(default_factory=RateLimitDefaults)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            TomlConfigSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _enforce_contract_hard_caps(self) -> Self:
        cap = self.system_hard_cap
        contract = SYSTEM_HARD_CAP
        if (
            cap.wall_clock_ms > contract.wall_clock_ms
            or cap.max_tool_calls > contract.max_tool_calls
            or cap.max_tokens > contract.max_tokens
        ):
            raise ValueError(
                "system_hard_cap in config exceeds contract SYSTEM_HARD_CAP "
                f"(got {cap}, contract {contract}). "
                "Raising the wire-level hard cap requires a contract version bump."
            )
        return self


def load_config(path: Path | None = None, **overrides: Any) -> KernelConfig:
    """Convenience factory. `overrides` take highest precedence (used in tests).

    If `path` is provided, points the TOML loader at an explicit file; else
    falls back to `var/kernel.toml` relative to the current working dir.
    """
    if path is not None:
        # Build a one-off subclass so the TOML source picks up the caller's path.
        class _Scoped(KernelConfig):
            model_config = SettingsConfigDict(
                toml_file=str(path),
                env_prefix="KERNEL_",
                env_nested_delimiter="__",
                extra="forbid",
                frozen=True,
            )

        return _Scoped(**overrides)
    return KernelConfig(**overrides)
