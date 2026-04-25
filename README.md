# Orchestrator Kernel MVP

This repository contains the orchestrator-kernel MVP, including CLI, HTTP, and Feishu stub entrypoints.

## Docker quick start

```bash
docker compose up --build
```

Then test the Feishu stub endpoint:

```bash
curl -X POST "http://127.0.0.1:8001/feishu/submit" \
  -H "Content-Type: application/json" \
  -d '{"text":"echo hello","userId":"alice","eventId":"01KQ1PHASE1000000000000000"}'
```

Health check:

```bash
curl http://127.0.0.1:8001/healthz
```
