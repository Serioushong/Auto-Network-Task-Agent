"""T044 — Typer-based CLI submit command (FR-003 / FR-030).

The module exposes two pieces:

- ``submit`` — a Typer command that accepts ``--text``, ``--event-id``,
  ``--user-id``, ``--audit-dir``, ``--worker``, ``--timeout``; it spawns
  the assembled kernel, registers each worker stub, runs a single event
  through the pipeline, and prints the resulting ``ResultSummary`` line
  to stdout (one JSON-Lines row, already validated against the
  contract).
- ``register(app)`` — helper that attaches ``submit`` (and ``status``) to
  any Typer application. ``cli_main.app`` calls this at import time so
  the ``orchestrator-kernel`` console script sees the commands.

Design notes:
- Defaults follow spec.md §P1 / quickstart.md §2: ``user-id`` falls back
  to the ``ORCHESTRATOR_USER`` environment variable before the hard-coded
  ``cli-user`` literal.
- ``source-channel`` is pinned to ``"cli"`` at the pipeline layer
  (``KernelHarness.submit`` always fills ``sourceChannel="cli"``). There
  is no flag to override it from the CLI for MVP — adding that belongs in
  US6 / T086 once multi-channel delivery lands.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import typer

_USER_ENV_VAR = "ORCHESTRATOR_USER"


def _default_user_id() -> str:
    """Resolve the default ``--user-id`` from the environment or fall back."""
    env_value = os.environ.get(_USER_ENV_VAR)
    return env_value.strip() if env_value and env_value.strip() else "cli-user"


def _default_echo_worker() -> Path:
    """Absolute path to the bundled echo-worker stub (T043)."""
    return (
        Path(__file__).resolve().parents[2] / "workers_stub" / "echo_worker.py"
    )


async def _submit_once(
    *,
    text: str,
    user_id: str,
    event_id: str | None,
    audit_dir: Path,
    worker_scripts: list[Path],
    timeout_s: float,
) -> None:
    """Spawn a single-shot kernel, register workers, submit, then tear down."""
    from ..cli_main import TraceResult, assemble_kernel

    harness = await assemble_kernel(audit_dir=audit_dir)
    result: TraceResult | None = None
    try:
        for script in worker_scripts:
            await harness.register_worker(
                SimpleNamespace(
                    script_path=script,
                    expected_capabilities=(),
                )
            )
        result = await harness.submit(
            text=text,
            user_id=user_id,
            event_id=event_id,
            timeout_s=timeout_s,
        )
    finally:
        await harness.shutdown()

    if result is None:  # pragma: no cover — only on teardown-crash paths
        typer.echo("submit failed before producing a result", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        f"traceId={result.traceId} outcome={result.traceOutcome} "
        f"duration={result.duration_s:.2f}s"
    )


def submit(
    text: Annotated[
        str,
        typer.Option("--text", help="Natural-language command (required)."),
    ],
    user_id: Annotated[
        str,
        typer.Option(
            "--user-id",
            help=(
                "User identifier for (userId, eventId) idempotency; defaults "
                f"to ${_USER_ENV_VAR} env var or 'cli-user'."
            ),
        ),
    ] = "",
    event_id: Annotated[
        str | None,
        typer.Option(
            "--event-id",
            help="Optional ULID-26 event id; auto-generated when absent.",
        ),
    ] = None,
    audit_dir: Annotated[
        Path,
        typer.Option(
            "--audit-dir",
            help="Directory for JSONL audit logs (created if missing).",
        ),
    ] = Path("var/audit"),
    worker_script: Annotated[
        list[Path] | None,
        typer.Option(
            "--worker",
            help=(
                "Path to a worker stub to spawn; repeat for multiple workers. "
                "Defaults to the bundled echo-worker."
            ),
        ),
    ] = None,
    timeout_s: Annotated[
        float,
        typer.Option("--timeout", help="Dispatch wait timeout (seconds)."),
    ] = 5.0,
) -> None:
    """Submit one event through the kernel and print its ResultSummary.

    The full JSON-form ``ResultSummary`` is also emitted on stdout by the
    notifier (``notifier.result_summary.print_to_cli``); this command's
    own ``typer.echo`` adds a final human-readable line with traceId +
    outcome + duration so operators can skim the CLI output.
    """
    resolved_user = user_id.strip() if user_id else _default_user_id()
    scripts = list(worker_script) if worker_script else [_default_echo_worker()]
    asyncio.run(
        _submit_once(
            text=text,
            user_id=resolved_user,
            event_id=event_id,
            audit_dir=audit_dir,
            worker_scripts=scripts,
            timeout_s=timeout_s,
        )
    )


def status() -> None:
    """Report harness status. Replaces the Phase 1 scaffold placeholder."""
    typer.echo(
        "orchestrator-kernel: Phase 3 US1 MVP live. "
        "Run `orchestrator-kernel submit --text 'echo hello'` for a demo."
    )


def register(app: typer.Typer) -> None:
    """Attach ``submit`` and ``status`` to a Typer app (called by cli_main)."""
    app.command()(submit)
    app.command()(status)


__all__ = ["register", "status", "submit"]
