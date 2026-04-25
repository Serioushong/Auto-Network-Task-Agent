# Daily Execution Runbook

This document is the source of truth for the local daily checks. Run it each day before making changes or handing the project off.

## Purpose

- Keep the local MVP healthy.
- Catch regressions early.
- Make sure the core workflow still works before any Feishu, subagent, or cloud work.

## How to run

Use **PowerShell** from the repository root.

```powershell
cd D:\Spec-Plan-Harness
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\python.exe -m pytest -q
```

## Pass criteria

- `ruff` exits successfully with no errors.
- `mypy` exits successfully with no errors.
- `pytest` passes with only expected skips.

Current baseline:

- `532 passed`
- `1 skipped`

## If something fails

Stop at the first failure and send me:

- the exact command you ran
- the full error output
- the time of the failure
- whether the failure is reproducible

Do not continue to the next step until the current failure is understood or fixed.

## Daily workflow

1. Open PowerShell in the repo root.
2. Run the three commands above.
3. If all three pass, the local MVP is healthy for the day.
4. If any command fails, report it and wait for an updated instruction set.

## Command sync rule

This document is maintained as the canonical daily script.

When I update the daily commands, I will update this file as well so it stays in sync.

If a new command is added for daily checks, append it here in the same order the checks should be run.

## Notes for future expansion

When the local daily checks stay green consistently, the next stages are:

- local MVP acceptance testing
- Feishu entry integration
- subagent execution integration
- cloud deployment last

Those stages will each get their own separate runbook, but this file remains the daily baseline.
