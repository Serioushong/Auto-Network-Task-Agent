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
from pydantic import ValidationError

_USER_ENV_VAR = "ORCHESTRATOR_USER"

_EVENT_ID_MIN = 16
_EVENT_ID_MAX = 64


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
    approval_timeout_ms: int,
    auto_response: str | None,
) -> None:
    """Spawn a single-shot kernel, register workers, submit, then tear down."""
    from ..cli_main import TraceResult, assemble_kernel

    harness = await assemble_kernel(
        audit_dir=audit_dir, approval_timeout_ms=approval_timeout_ms
    )
    result: TraceResult | None = None
    try:
        for script in worker_scripts:
            await harness.register_worker(
                SimpleNamespace(
                    script_path=script,
                    expected_capabilities=(),
                )
            )
        submit_task = asyncio.create_task(
            harness.submit(
                text=text,
                user_id=user_id,
                event_id=event_id,
                timeout_s=timeout_s,
            )
        )
        if auto_response is not None:
            asyncio.create_task(
                _auto_respond(
                    harness=harness,
                    user_id=user_id,
                    decision=auto_response,
                )
            )
        result = await submit_task
    finally:
        await harness.shutdown()

    if result is None:  # pragma: no cover — only on teardown-crash paths
        typer.echo("submit failed before producing a result", err=True)
        raise typer.Exit(code=2)

    typer.echo(
        f"traceId={result.traceId} outcome={result.traceOutcome} "
        f"duration={result.duration_s:.2f}s"
    )


async def _auto_respond(
    *, harness: object, user_id: str, decision: str
) -> None:
    """Small helper used by ``--auto-approve`` / ``--auto-deny``.

    Polls the harness for the first ``pending_approval`` trace then
    submits the decision. Single-process convenience only — cross-
    process CLI ``approve`` / ``deny`` requires the Phase 7 daemon
    mode that exposes a local socket endpoint.
    """
    for _ in range(50):
        await asyncio.sleep(0.05)
        events = getattr(harness, "_approval_events", {})
        if events:
            trace_id = next(iter(events.keys()))
            await harness.submit_approval_response(  # type: ignore[attr-defined]
                trace_id=trace_id, decision=decision, user_id=user_id
            )
            return


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
            help=(
                "Optional event id; auto-generated when absent. "
                f"Must be {_EVENT_ID_MIN}-{_EVENT_ID_MAX} characters "
                "(ULID-26 recommended, e.g. '01HYZAB...')."
            ),
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
    approval_timeout_ms: Annotated[
        int,
        typer.Option(
            "--approval-timeout-ms",
            help=(
                "HIGH_RISK approval window in milliseconds (FR-011). "
                "Defaults to 600_000 (10 min)."
            ),
        ),
    ] = 600_000,
    auto_approve: Annotated[
        bool,
        typer.Option(
            "--auto-approve",
            help="Single-process demo: auto-approve any HIGH_RISK request.",
        ),
    ] = False,
    auto_deny: Annotated[
        bool,
        typer.Option(
            "--auto-deny",
            help="Single-process demo: auto-deny any HIGH_RISK request.",
        ),
    ] = False,
) -> None:
    """Submit one event through the kernel and print its ResultSummary.

    The full JSON-form ``ResultSummary`` is also emitted on stdout by the
    notifier (``notifier.result_summary.print_to_cli``); this command's
    own ``typer.echo`` adds a final human-readable line with traceId +
    outcome + duration so operators can skim the CLI output.

    HIGH_RISK notes
    ---------------
    A HIGH_RISK command (e.g. ``delete fake.txt``) parks in
    ``pending_approval`` and blocks for up to ``--approval-timeout-ms``.
    For single-process demos use ``--auto-approve`` / ``--auto-deny``.
    Cross-process CLI ``approve`` / ``deny`` requires the Phase 7 daemon
    mode (see ``status`` output).
    """
    if auto_approve and auto_deny:
        raise typer.BadParameter(
            "--auto-approve and --auto-deny are mutually exclusive"
        )
    if event_id is not None:
        length = len(event_id)
        if length < _EVENT_ID_MIN or length > _EVENT_ID_MAX:
            typer.echo(
                f"error: --event-id must be {_EVENT_ID_MIN}-{_EVENT_ID_MAX} "
                f"characters (got {length}). Use a ULID-26 such as "
                "'01HYZAB' padded to 16+ chars, or omit the flag to "
                "auto-generate one.",
                err=True,
            )
            raise typer.Exit(code=2)
    auto_response: str | None
    if auto_approve:
        auto_response = "approve"
    elif auto_deny:
        auto_response = "deny"
    else:
        auto_response = None
    resolved_user = user_id.strip() if user_id else _default_user_id()
    scripts = list(worker_script) if worker_script else [_default_echo_worker()]
    try:
        asyncio.run(
            _submit_once(
                text=text,
                user_id=resolved_user,
                event_id=event_id,
                audit_dir=audit_dir,
                worker_scripts=scripts,
                timeout_s=timeout_s,
                approval_timeout_ms=approval_timeout_ms,
                auto_response=auto_response,
            )
        )
    except ValidationError as exc:
        # Catch-all safety net: any contract-level validation that
        # slipped past CLI-side pre-checks (e.g. a future schema
        # tightening) surfaces here as a single friendly line
        # instead of a raw pydantic traceback.
        first = exc.errors()[0] if exc.errors() else {"msg": str(exc)}
        loc = ".".join(str(p) for p in first.get("loc", ())) or "payload"
        typer.echo(
            f"error: contract validation failed on '{loc}': "
            f"{first.get('msg', 'invalid value')}. See "
            "`orchestrator-kernel submit --help` for flag constraints.",
            err=True,
        )
        raise typer.Exit(code=2) from exc


def status() -> None:
    """Report harness status. Replaces the Phase 1 scaffold placeholder."""
    typer.echo(
        "orchestrator-kernel: Phase 6 US4 live. "
        "US1 (echo), US2 (in-process idempotency), US3 (HIGH_RISK approval gate), "
        "US4 (cancel / signal escalation) are green. "
        "Demos: `submit --text 'echo hello'`, "
        "`submit --text 'delete foo.txt' --auto-approve`, "
        "`submit --text 'sleep 10'` + in-process `request_cancel(...)`. "
        "Cross-process `approve` / `deny` / `cancel` is queued for Phase 7 "
        "daemon mode."
    )


def approve(
    trace_id: Annotated[
        str,
        typer.Argument(
            help="Trace ID returned by a previous HIGH_RISK submit."
        ),
    ],
    user_id: Annotated[
        str,
        typer.Option(
            "--user-id",
            help=(
                "Approver identity; MUST match the submitter "
                f"(fallback ${_USER_ENV_VAR} env var)."
            ),
        ),
    ] = "",
) -> None:
    """Approve a pending HIGH_RISK trace (requires Phase 7 daemon mode)."""
    _ = trace_id, user_id
    typer.echo(
        "error: cross-process `approve` requires the Phase 7 daemon mode "
        "(T084+). For single-process demos, pass --auto-approve to "
        "`orchestrator-kernel submit`.",
        err=True,
    )
    raise typer.Exit(code=2)


def deny(
    trace_id: Annotated[
        str,
        typer.Argument(help="Trace ID returned by a previous HIGH_RISK submit."),
    ],
    user_id: Annotated[
        str,
        typer.Option(
            "--user-id",
            help=(
                "Denier identity; MUST match the submitter "
                f"(fallback ${_USER_ENV_VAR} env var)."
            ),
        ),
    ] = "",
) -> None:
    """Deny a pending HIGH_RISK trace (requires Phase 7 daemon mode)."""
    _ = trace_id, user_id
    typer.echo(
        "error: cross-process `deny` requires the Phase 7 daemon mode "
        "(T084+). For single-process demos, pass --auto-deny to "
        "`orchestrator-kernel submit`.",
        err=True,
    )
    raise typer.Exit(code=2)


def cancel(
    trace_id: Annotated[
        str,
        typer.Argument(help="Trace ID returned by a previous submit."),
    ],
    user_id: Annotated[
        str,
        typer.Option(
            "--user-id",
            help=(
                "Canceller identity; MUST match the submitter "
                f"(fallback ${_USER_ENV_VAR} env var)."
            ),
        ),
    ] = "",
) -> None:
    """Cancel a live trace (requires Phase 7 daemon mode).

    MVP is single-process: cancel drives through
    ``KernelHarness.request_cancel`` within the same ``submit`` invocation.
    Integration tests exercise the full state machine in-process;
    cross-process ``cancel`` waits on the daemon-mode socket endpoint
    (Phase 7 T084+).
    """
    _ = trace_id, user_id
    typer.echo(
        "error: cross-process `cancel` requires the Phase 7 daemon mode "
        "(T084+). Single-process cancel is available via "
        "`KernelHarness.request_cancel` in integration tests.",
        err=True,
    )
    raise typer.Exit(code=2)


def register(app: typer.Typer) -> None:
    """Attach CLI commands to a Typer app (called by cli_main)."""
    app.command()(submit)
    app.command()(status)
    app.command()(approve)
    app.command()(deny)
    app.command()(cancel)


__all__ = ["approve", "cancel", "deny", "register", "status", "submit"]
