# CLI and HTTP Smoke Checklist

Use this after the daily health checks pass.

## Prerequisites

Run the daily script first:

```powershell
.\docs\daily-execution-script.ps1
```

## CLI smoke test

### Command

```powershell
cd D:\Spec-Plan-Harness
.\.venv\Scripts\python.exe -m orchestrator_kernel.cli_main submit "echo hello"
```

### Expected result

- The command completes successfully.
- A trace result is printed.
- The trace outcome indicates success.
- Audit output is written for the trace lifecycle.

### If it fails

Capture:

- the exact command
- the full terminal output
- any audit log lines around the failure

---

## HTTP smoke test

### Supported local verification command

The project currently verifies the HTTP entry via the integration test harness rather than a standalone `uvicorn` command. Use this command to exercise the HTTP path locally:

```powershell
cd D:\Spec-Plan-Harness
.\.venv\Scripts\python.exe -m pytest tests\integration\test_http_entry.py -q
```

### Why this is the supported command

- `orchestrator_kernel.entrypoints.http` is a FastAPI app factory.
- The tests mount it directly through `httpx.ASGITransport`.
- There is currently no separate `uvicorn` launch script in the repo.

### Expected result

- HTTP entry tests pass.
- `POST /submit` is exercised through the same kernel pipeline as the CLI.
- `GET /healthz` reports the kernel as ready.
- Audit trail includes the received event and summary delivery steps.

### If it fails

Capture:

- the test command
- the failing test name
- the HTTP status code or assertion message
- the full test output

---

## Rejection smoke tests

Run these after the happy path passes.

### Oversize payload rejection

```powershell
$big = 'A' * (32 * 1024)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:<port>/submit -ContentType application/json -Body (@{ text = $big; userId = 'alice' } | ConvertTo-Json)
```

Expected:

- Request is rejected quickly.
- Rejection is audited.
- Payload guard runs before deeper validation.

### Idempotency smoke

```powershell
$body = @{ text = 'echo idem'; userId = 'alice'; eventId = '01HXTEST000IDEMHTTP00' }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:<port>/submit -ContentType application/json -Body ($body | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:<port>/submit -ContentType application/json -Body ($body | ConvertTo-Json)
```

Expected:

- Second call dedupes or replays.
- No duplicate work is performed.

---

## Order of execution

1. Daily health checks
2. CLI happy path
3. HTTP entry tests via `pytest tests/integration/test_http_entry.py -q`
4. Oversize rejection
5. Idempotency replay

## Maintenance rule

If the CLI or HTTP commands change, update this file immediately so it stays current.
