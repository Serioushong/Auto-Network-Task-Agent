<#
.SYNOPSIS
  Phase N smoke / regression baseline for Orchestrator Kernel.

.DESCRIPTION
  Exercises the two MVP-gap closures landed in Phase N:

    - Round 1 / T076 — Heartbeat + HeartbeatTracker + Dispatcher.set_health
      (`tests/unit/test_heartbeat_tracker.py` + `tests/integration/test_worker_heartbeat.py`)

    - Round 2 / T074 — Windows Job Object + POSIX rlimit + sandbox wiring
      (`tests/unit/test_supervisor_sandbox.py` + `tests/unit/test_sandbox_wiring.py`)

    - Round 3 / Evidence #14 — real Windows Job Object OOM enforcement
      (`tests/integration/test_job_object_oom.py`): end-to-end proof that
      `_bind_job_object` + `CREATE_BREAKAWAY_FROM_JOB` + two-stage
      `bind_sandbox` actually cause the Windows kernel to kill a worker
      trying to blast past the declared `memory_mb` cap.

  Then runs the full regression + ruff + mypy so the script is usable as
  a one-shot "is the kernel still Phase-N-green?" check.

  Exit codes:
    0  — all phases green
    1  — at least one phase failed; scroll up for the first red line

.PARAMETER SkipFullRegression
  Skip the final `pytest -q` full-suite step. Useful for tight iteration
  loops when you only want the Phase-N targeted subsets + lint/type.

.PARAMETER SkipLint
  Skip `ruff check .` and `mypy src`. Not recommended as part of a real
  baseline — use only when actively landing lint-sensitive changes.

.EXAMPLE
  pwsh -File scripts/smoke-phase-n.ps1

.EXAMPLE
  pwsh -File scripts/smoke-phase-n.ps1 -SkipFullRegression

.NOTES
  Expected totals (as of Evidence #14 in validation.md):
    - Phase N.1 targeted: 8 passed   (7 tracker + 1 integration)
    - Phase N.2 targeted: 4 passed + 1 skipped  (POSIX-only rlimit skip)
    - Phase N.3 targeted: 1 passed   (Windows only; 1 skip on POSIX)
    - Full regression:    481 passed + 1 skipped  (+1 on Windows)
    - ruff + mypy:        all green
#>
[CmdletBinding()]
param(
    [switch]$SkipFullRegression,
    [switch]$SkipLint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtualenv python not found at '$venvPython'. Did you run 'uv sync' / create .venv?"
    exit 1
}

# --- phase bookkeeping ------------------------------------------------------
$phaseResults = [System.Collections.Generic.List[pscustomobject]]::new()

function Invoke-Phase {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string[]]$ArgumentList
    )

    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host ">> $Name" -ForegroundColor Cyan
    Write-Host ('  $ ' + $venvPython + ' ' + ($ArgumentList -join ' ')) -ForegroundColor DarkGray
    Write-Host ('=' * 72) -ForegroundColor Cyan

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $venvPython @ArgumentList
    $exit = $LASTEXITCODE
    $sw.Stop()

    $status = if ($exit -eq 0) { 'PASS' } else { 'FAIL' }
    $color  = if ($exit -eq 0) { 'Green' } else { 'Red' }
    $elapsed = '{0:N2}s' -f $sw.Elapsed.TotalSeconds
    Write-Host ''
    Write-Host ("  -> $status  ($elapsed, exit=$exit)") -ForegroundColor $color

    $phaseResults.Add([pscustomobject]@{
        Name    = $Name
        Status  = $status
        Exit    = $exit
        Elapsed = $elapsed
    }) | Out-Null

    return $exit
}

# --- 0. environment sanity --------------------------------------------------
Write-Host ''
Write-Host 'Phase N smoke / regression baseline' -ForegroundColor Yellow
Write-Host "Repo root : $repoRoot"
Write-Host "Python    : $venvPython"
& $venvPython --version

# --- 1. Phase N.1 — Heartbeat targeted subset -------------------------------
Invoke-Phase -Name 'Phase N.1 - Heartbeat (tracker + silent_worker integration)' -ArgumentList @(
    '-m', 'pytest',
    'tests/unit/test_heartbeat_tracker.py',
    'tests/integration/test_worker_heartbeat.py',
    '-v'
) | Out-Null

# --- 2. Phase N.2 — Sandbox targeted subset ---------------------------------
Invoke-Phase -Name 'Phase N.2 - Sandbox (Job Object + wiring)' -ArgumentList @(
    '-m', 'pytest',
    'tests/unit/test_supervisor_sandbox.py',
    'tests/unit/test_sandbox_wiring.py',
    '-v'
) | Out-Null

# --- 2b. Evidence #14 — real Windows Job Object OOM enforcement (no mocks) -
# POSIX-skipped inside the test; safe to always invoke. The test actually
# spawns oom_blast_worker, assigns it to a 64 MiB Job Object, and asserts
# the Windows kernel kills it before it burns 128 MiB. This is the single
# highest-signal regression for sandbox breakage because any future edit
# to supervisor.spawn / bind_sandbox / _bind_job_object that silently
# disables enforcement will turn this test red within seconds.
Invoke-Phase -Name 'Phase N.3 - Real Job Object OOM (Evidence #14)' -ArgumentList @(
    '-m', 'pytest',
    'tests/integration/test_job_object_oom.py',
    '-v'
) | Out-Null

# --- 3. lint + type ---------------------------------------------------------
if (-not $SkipLint) {
    Invoke-Phase -Name 'ruff check .' -ArgumentList @('-m', 'ruff', 'check', '.') | Out-Null
    Invoke-Phase -Name 'mypy src'      -ArgumentList @('-m', 'mypy', 'src')       | Out-Null
}
else {
    Write-Host ''
    Write-Host 'ruff + mypy skipped (-SkipLint).' -ForegroundColor DarkYellow
}

# --- 4. full regression -----------------------------------------------------
if (-not $SkipFullRegression) {
    Invoke-Phase -Name 'Full regression (pytest -q)' -ArgumentList @('-m', 'pytest', '-q') | Out-Null
}
else {
    Write-Host ''
    Write-Host 'Full regression skipped (-SkipFullRegression).' -ForegroundColor DarkYellow
}

# --- 5. summary -------------------------------------------------------------
Write-Host ''
Write-Host ('=' * 72) -ForegroundColor Yellow
Write-Host 'Phase N smoke summary' -ForegroundColor Yellow
Write-Host ('=' * 72) -ForegroundColor Yellow

$phaseResults | Format-Table -AutoSize | Out-String | Write-Host

# Array-wrap defensively: `Where-Object` returns $null when nothing matches,
# and Set-StrictMode then trips on `.Count`. @(...) normalises to an array.
$failed = @($phaseResults | Where-Object { $_.Status -ne 'PASS' })
if ($failed.Count -gt 0) {
    Write-Host ("FAIL  {0} phase(s) failed." -f $failed.Count) -ForegroundColor Red
    exit 1
}

Write-Host 'PASS  All phases green -- Phase N baseline intact.' -ForegroundColor Green
exit 0
