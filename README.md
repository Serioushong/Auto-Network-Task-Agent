# Orchestrator Kernel MVP

This repository contains the orchestrator-kernel MVP, including CLI, HTTP, and Feishu integration entrypoints.

## Feishu SDK quick start

This project now prefers the official Feishu Python SDK long-connection mode for event reception.
It does not require a public webhook URL, encryption strategy, or challenge-based callback configuration.

Set the Feishu application credentials in your environment:

```bash
export FEISHU_APP_ID=your_app_id
export FEISHU_APP_SECRET=your_app_secret
```

Then start the SDK listener:

```bash
python -m orchestrator_kernel.entrypoints.feishu_sdk
```

Or, with Docker Compose:

```bash
docker compose up --build
```

The SDK listener receives the **消息与群组 -> 接收消息 v2.0** event, maps it into the kernel's internal payload shape, and routes it through the shared Phase 10 execution path.

## Feishu event payload debugging

The SDK and webhook-compatible entrypoints log the mapped payload so you can confirm the following fields during development:

- `text`
- `userId`
- `eventId`

These are derived from the official receive-message event structure.

## Legacy webhook compatibility

The repository still contains the webhook-compatible Feishu stub for debugging and compatibility testing, but SDK mode is the recommended path for new development.

## Health check

```bash
curl http://127.0.0.1:8001/healthz
```
