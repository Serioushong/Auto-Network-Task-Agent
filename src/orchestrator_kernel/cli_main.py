"""CLI entrypoint placeholder.

Real assembly happens in later tasks (see tasks.md Phase 2C / Polish T097–T100).
This stub exists only so the `orchestrator-kernel` console-script entry-point in
`pyproject.toml` resolves cleanly during `uv sync` / editable install.
"""

from __future__ import annotations

import typer

_HELP = (
    "Orchestrator Kernel MVP — not yet implemented "
    "(see specs/001-orchestrator-kernel/tasks.md)."
)

app = typer.Typer(
    name="orchestrator-kernel",
    help=_HELP,
    no_args_is_help=True,
)


@app.command()
def status() -> None:
    """Report scaffold status. Replaced once real entrypoints land."""
    typer.echo(
        "orchestrator-kernel: scaffold present, kernel not yet implemented. "
        "Run /speckit-implement T007+ to begin the TDD loop."
    )


if __name__ == "__main__":
    app()
