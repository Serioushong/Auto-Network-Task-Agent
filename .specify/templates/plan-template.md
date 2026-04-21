# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]  
**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]  
**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]  
**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]  
**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]
**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]  
**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]  
**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]  
**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
*Aligned with `.specify/memory/constitution.md` v1.0.0.*

| # | Gate | Pass criteria | Status |
|---|------|---------------|--------|
| I   | Spec-Driven Development          | 本功能已走完 specify→clarify→plan→tasks→analyze 工序 | ☐ PASS / ☐ FAIL |
| II  | Least Privilege (Capability Matrix) | 本 plan 列出了全部 capability；HIGH_RISK 动作已独立标注 | ☐ PASS / ☐ FAIL |
| III | Human-in-the-Loop Gate           | HIGH_RISK 动作均有确认流程；主 Agent 支持一键取消 | ☐ PASS / ☐ FAIL / ☐ N/A |
| IV  | Observability & Auditability     | 设计了 traceId/taskId 结构化日志与敏感字段脱敏方案 | ☐ PASS / ☐ FAIL |
| V   | Sandboxed Execution              | 子 Agent 有进程/容器隔离 + 资源上限 + 软中止 | ☐ PASS / ☐ FAIL / ☐ N/A |
| VI  | Idempotent & Replayable          | 入口事件与任务具备幂等键；支持只读重放 | ☐ PASS / ☐ FAIL |
| VII | Contract-First                   | 跨 Agent/外部边界的消息 Schema 已先行定义 | ☐ PASS / ☐ FAIL |
| VIII| Branch-Based Dual-Gate Release   | 开发分支为 `hjx`；合并 `main` 的四项条件已规划 | ☐ PASS / ☐ FAIL |

> 任何 FAIL 必须在 `## Complexity Tracking` 记录豁免理由或改写 plan。
> 任何 N/A 必须在同节说明"本功能为何不涉及该原则"。

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
