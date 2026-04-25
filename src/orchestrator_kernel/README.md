# Orchestrator Kernel

This package contains the kernel for the multi-agent orchestration MVP.
It is organized around a contract-first intake pipeline, audit logging,
worker supervision, and notifier delivery.

## Entry points

- `orchestrator_kernel.cli_main:app`
  - Primary CLI application for local submission and operational commands.
- `orchestrator_kernel.entrypoints.http:create_app`
  - Local FastAPI entry stub that reuses the same intake pipeline as the CLI.
- `orchestrator_kernel.entrypoints.feishu_stub:main`
  - Placeholder channel stub used to demonstrate extensibility.

## Subpackage responsibilities

- `contracts/`
  - Pydantic models and schema mirrors for kernel messages and records.
- `entrypoints/`
  - External intake surfaces such as CLI, HTTP, and future channel stubs.
- `kernel/`
  - Core orchestration logic: state machine, idempotency, approval, cancel,
    task planning, rate limiting, and input validation.
- `worker_supervisor/`
  - Subprocess lifecycle management, protocol framing, heartbeat tracking,
    sandbox wiring, and escalation.
- `audit/`
  - Structured audit event writing, hashing, redaction, and recovery scan.
- `notifier/`
  - ResultSummary construction and delivery retry logic.
- `llm/`
  - Protocol boundary for a future LLM-backed planner.
- `workers_stub/`
  - Local worker stubs used by integration tests and developer smoke runs.

## Development recap

1. Use `uv sync` to install the pinned toolchain.
2. Run `uv run pytest -q`, `uv run ruff check src tests`, and `uv run mypy src`.
3. Exercise a quick end-to-end submit via CLI or HTTP before expanding scope.
4. Keep `tasks.md` and `validation.md` in sync with every execution step.

## Notes

- This repository follows the spec-first flow described in the constitution.
- `main` remains protected; development work happens on `hjx`.
