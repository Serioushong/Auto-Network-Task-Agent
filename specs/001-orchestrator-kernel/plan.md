# Implementation Plan: Orchestrator Kernel (主 Agent 编排内核 MVP)

**Branch**: `hjx` (per Constitution Article VIII — no per-feature branch)
**Date**: 2026-04-21
**Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-orchestrator-kernel/spec.md`
**Constitution**: `.specify/memory/constitution.md` v1.0.0

## Summary

构建一个**纯内核级**的主 Agent 编排内核 MVP：接收统一入口事件 (CLI / 本地 HTTP 预留)，
转化为 Task 树，按 capability 分派给**以子进程形式独立运行**的 stub Worker，
全过程维护任务生命周期、**HIGH_RISK 人工审批**、**一键取消**、**幂等去重**、
**崩溃补终态 + 结果主动回推**、**结构化 JSONL 审计日志**。

技术路线：**Python 3.11+ + asyncio + pydantic v2 + subprocess JSON-lines stdio 协议**。
所有跨边界消息（入口事件 / Task 派发 / Worker 回传 / 审批 / 取消 / 结果汇总）
在 Phase 1 以 pydantic model + JSON Schema **先行定义**（Constitution VII 契约先行）。
Worker 在 MVP 阶段**全部为 stub**（echo / sleep / crash / danger-file-delete 四种），
飞书入口与真实桌面操控延后到 002、003 feature。

## Technical Context

**Language/Version**: Python 3.11+ (CPython)
**Primary Dependencies**: pydantic v2 (schema & 契约), anyio/asyncio (并发编排), structlog (结构化日志), typer (CLI 入口), psutil (Worker 资源监控), pytest / pytest-asyncio / hypothesis (测试)
**Storage**: 本地文件系统
  - 审计日志：JSONL，按日 rotation，路径 `./var/audit/YYYY-MM-DD.jsonl`，保留 ≥ 30 天（FR-021）
  - 幂等缓存：纯内存 `dict[(userId, eventId)] → traceId`；冷启动由审计扫描重建（FR-028）
  - 无数据库依赖
**Testing**: pytest + pytest-asyncio (异步协程) + hypothesis (幂等/状态机属性测试)；覆盖契约测试、集成测试、单元测试三层
**Target Platform**: Windows 10+ with PowerShell 5.1 (一级支持，Constitution 硬性要求)；Linux / macOS 尽力而为 (stdlib 层面兼容，不专门测试)
**Project Type**: 单项目 (Python package) —— CLI + 服务内核，非 web/mobile
**Performance Goals** (映射 spec Success Criteria):
  - SC-002: stub Worker 耗时 ≤ 500 ms 前提下，端到端 95 分位 ≤ 3 秒
  - SC-004: 一键取消 ≤ 5 秒生效（实测目标 ≤ 2 秒）
  - SC-008: 审计磁盘故障 10 秒内切入"拒绝新入口"状态
  - SC-009: 崩溃后 ≤ 10 秒完成审计扫描 + 结果回推
  - SC-011: 超大 payload 拒绝 ≤ 50 ms
**Constraints**:
  - 内核主进程内存 ≤ 256 MB
  - 默认 Worker 上限 128 MB / 1 CPU / 60 s 墙钟（可按 capability 声明覆写，硬顶 FR-018）
  - 敏感字段（凭据、长文本）一律 SHA-256 hash 或 mask，禁止明文入日志（FR-020）
  - MVP 不真连任何 LLM；LLMClient Protocol 预留抽象，Worker 为 stub
**Scale/Scope**:
  - 单机单内核进程 + ≤ 8 个并发 Worker 子进程
  - 并发活跃 trace ≤ 50（匹配 FR-026 全局 50 events/sec）
  - 每用户并发：NORMAL ≤ 10 trace / HIGH_RISK ≤ 1 Task (FR-025)
  - 代码规模目标：核心内核 ≤ 3000 LOC；stub Workers ≤ 500 LOC；测试 ≥ 2× 生产代码

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
*Aligned with `.specify/memory/constitution.md` v1.0.0.*

### Initial Gate (pre-research)

| # | Gate | Pass criteria | Status | Evidence |
|---|------|---------------|--------|----------|
| I   | Spec-Driven Development             | specify ✓ clarify ✓；plan 进行中 → tasks → analyze 未启动但已排期                       | ☑ PASS | spec.md 已关闭 5 项 NEEDS CLARIFICATION |
| II  | Least Privilege (Capability Matrix) | capability 在 Worker 注册时声明；HIGH_RISK 独立标注并走 III 审批门                         | ☑ PASS | FR-007/025 + data-model 中 `Capability` / `Worker` |
| III | Human-in-the-Loop Gate              | HIGH_RISK 走 `pending_approval`；内核原生支持 `cancel <traceId>` ≤ 5 秒                   | ☑ PASS | FR-010~015 + SC-004 + `ApprovalMessage` schema |
| IV  | Observability & Auditability        | JSONL 结构化日志；traceId 可完整回放；敏感字段脱敏；磁盘故障拒绝新事件                       | ☑ PASS | FR-019~021 + SC-006/008 + `AuditEvent` schema |
| V   | Sandboxed Execution                 | subprocess 隔离 + Windows Job Object 资源限制 + 软中止 → 硬终止 升级                       | ☑ PASS | FR-016~018 + SC-005 + Worker Supervisor 设计 |
| VI  | Idempotent & Replayable             | `(userId, eventId)` 幂等键；审计为事实来源；崩溃后 Fail-fast 补终态 + 结果回推                | ☑ PASS | FR-002/022/028~030 + SC-009/010 |
| VII | Contract-First                      | Phase 1 产出 `contracts/*.schema.json` + pydantic model；实现只消费已定义 schema             | ☑ PASS | 本 plan Phase 1 / contracts 目录 |
| VIII| Branch-Based Dual-Gate Release      | 所有提交落 `hjx`；`main` 合并需满足 analyze/TDD/人工验收/SemVer 四条                        | ☑ PASS | spec Feature Branch 字段 + 本 plan header |

> **结论**：Initial gate 全通过，无需 Complexity Tracking 条目。Phase 1 完成后将在本节下方追加 "Post-Design Gate" 复核。

### Post-Design Gate (filled at end of Phase 1)

> Phase 1 产出 data-model.md、contracts/*.schema.json、quickstart.md 后，
> 在此追加一张同构表格 + 差异说明。见文末 [Post-Design Gate](#post-design-gate-post-phase-1)。

## Project Structure

### Documentation (this feature)

```text
specs/001-orchestrator-kernel/
├── spec.md                   # 已完成（specify + clarify）
├── plan.md                   # 本文件（/speckit-plan 输出）
├── research.md               # Phase 0 输出（技术决策与替代方案）
├── data-model.md             # Phase 1 输出（实体 + 状态机）
├── quickstart.md             # Phase 1 输出（开发 / 验证入门）
├── contracts/                # Phase 1 输出（所有边界消息 schema）
│   ├── entry-event.schema.json
│   ├── task.schema.json
│   ├── worker-registration.schema.json
│   ├── worker-protocol.schema.json
│   ├── approval-message.schema.json
│   ├── cancel-message.schema.json
│   ├── audit-event.schema.json
│   └── result-summary.schema.json
├── checklists/
│   └── requirements.md       # 已完成
└── tasks.md                  # Phase 2 输出（/speckit-tasks，本命令不产出）
```

### Source Code (repository root)

```text
src/orchestrator_kernel/
├── __init__.py
├── config.py                 # 配置加载（含系统硬顶、默认阈值）
├── contracts/                # 由 contracts/*.schema.json 生成 / 手写 pydantic 镜像
│   ├── __init__.py
│   ├── entry_event.py
│   ├── task.py
│   ├── worker.py
│   ├── approval.py
│   ├── cancel.py
│   ├── audit.py
│   └── result_summary.py
├── entrypoints/              # 入口通道
│   ├── cli.py                # typer 实现，MVP 强制交付
│   ├── http.py               # FastAPI 本地预留，Phase 2 实装
│   └── feishu_stub.py        # stub，仅打印，002 feature 真实对接
├── kernel/
│   ├── state_machine.py      # Task 状态机（单向转换）
│   ├── task_tree.py          # Task 树构建与遍历
│   ├── idempotency.py        # (userId, eventId) 幂等缓存 + 审计重建
│   ├── rate_limit.py         # FR-025/026/027 限流与并发
│   ├── approval_gate.py      # HIGH_RISK 审批流
│   ├── cancel.py             # 一键取消 + 软中止升级
│   ├── budget.py             # 三维预算 (wall/tool/token) 监控
│   └── dispatcher.py         # capability 匹配与分派
├── worker_supervisor/
│   ├── supervisor.py         # subprocess + Job Object + psutil 监控
│   ├── protocol.py           # stdio JSON-lines 读写
│   └── lifecycle.py          # 心跳 / 健康探测 / unhealthy 标记
├── audit/
│   ├── writer.py             # JSONL 写入 + 按日 rotation + 磁盘故障检测
│   ├── scanner.py            # FR-028 启动时审计扫描补终态
│   ├── redact.py             # 敏感字段 hash / mask
│   └── hasher.py             # SHA-256 截断前 16 byte
├── notifier/
│   ├── result_summary.py     # FR-029 终态汇总消息生成
│   └── delivery.py           # 来源通道回推 + 3 次重试
├── llm/
│   └── client.py             # LLMClient Protocol（MVP 不实现具体厂商）
└── cli_main.py               # typer 入口装配

src/workers_stub/             # MVP 阶段 stub Worker（独立可执行脚本）
├── echo_worker.py            # capability: echo.say (NORMAL)
├── sleep_worker.py           # capability: sleep.wait (NORMAL)，用于取消 / 超时测试
├── crash_worker.py           # capability: crash.oom | crash.raise (NORMAL)，用于 P5 崩溃隔离测试
└── danger_worker.py          # capability: file.delete (HIGH_RISK)，用于 P3 审批流测试

tests/
├── contract/                 # 按 contracts/*.schema.json 校验 pydantic round-trip
│   ├── test_entry_event.py
│   ├── test_task.py
│   ├── test_worker_protocol.py
│   ├── test_approval.py
│   ├── test_cancel.py
│   ├── test_audit_event.py
│   └── test_result_summary.py
├── integration/              # 按 P1~P5 User Story 的 Independent Test 组织
│   ├── test_p1_basic_loop.py
│   ├── test_p2_idempotency.py           # + hypothesis 属性测试
│   ├── test_p3_approval_gate.py
│   ├── test_p4_cancel.py
│   ├── test_p5_crash_isolation.py
│   ├── test_kernel_restart_recovery.py  # FR-028/029 + SC-009
│   ├── test_rate_limit.py               # FR-025~027
│   ├── test_payload_size.py             # FR-031 + SC-011
│   └── test_result_notification.py      # FR-029/030 + SC-010
└── unit/
    ├── test_state_machine.py
    ├── test_idempotency_cache.py
    ├── test_audit_redact.py
    ├── test_audit_scanner.py
    ├── test_budget_monitor.py
    └── test_dispatcher.py

.specify/                     # 已存在，spec-kit 运行时
var/                          # 运行期产物（.gitignore 已排除）
├── audit/                    # JSONL 按日 rotation
└── tmp/                      # 测试临时文件

pyproject.toml                # uv / pip 兼容，声明依赖与 entry_points
uv.lock                       # uv 锁定文件
README.md                     # 已存在
```

**Structure Decision**: 选 **Option 1 (Single project)**。理由：
1. 内核 + Worker + 入口 + 审计全在单一 Python package 下，无跨语言边界；
2. Worker 虽为独立进程，但脚本仍由同一 package 提供（`src/workers_stub/`），避免分仓复杂度；
3. 飞书入口、真实桌面操控等异构组件延后到后续 feature，届时再评估是否拆 package；
4. 宪法 Additional Constraints 要求 Windows PowerShell 5.1 可完整运行 —— 单 Python 项目最省部署复杂度。

## Phase 0 — Outline & Research

**Output**: [research.md](./research.md)

**Unknowns extracted from Technical Context**: 0 (Technical Context 无 NEEDS CLARIFICATION —— spec 阶段已关闭全部 5 项；技术选型均在本 plan "Summary" 与 Technical Context 中直接固化。)

**Research tasks executed** (均在 research.md 中记录 Decision / Rationale / Alternatives considered):

1. **Worker 隔离机制选型**：subprocess + Windows Job Object vs Docker vs Firecracker vs WASM
2. **审计日志存储选型**：JSONL 文件 vs SQLite vs 外部日志服务
3. **幂等键存储选型**：内存 dict + 审计重建 vs Redis vs SQLite
4. **契约 schema 工具选型**：pydantic v2 vs JSON Schema 手写 vs Protobuf
5. **CLI 框架选型**：typer vs click vs argparse
6. **结构化日志库选型**：structlog vs logging + JSONFormatter vs loguru
7. **属性测试 / 幂等验证工具**：hypothesis vs 手写 fuzz 循环
8. **Windows 软中止信号语义**：CTRL_BREAK_EVENT vs taskkill /T vs Job Object 终止
9. **来源通道回推与重试语义**：exponential backoff 参数、失败降级策略
10. **LLM 抽象层最小形态**：Protocol vs ABC vs 无接口仅占位

## Phase 1 — Design & Contracts

**Outputs**:
- [data-model.md](./data-model.md)
- [contracts/](./contracts/)
- [quickstart.md](./quickstart.md)
- `.cursor/rules/specify-rules.mdc` 更新（指向本 plan）

**Contract boundaries identified**（对应 FR-024 所列 5 类 + 扩展至 8 类）:

| Schema 文件 | 覆盖边界 | FR 锚点 |
|---|---|---|
| `entry-event.schema.json` | 外部入口 → 内核 | FR-001, FR-031 |
| `task.schema.json` | 内核内部 Task 树与状态机 | FR-004, FR-005 |
| `worker-registration.schema.json` | Worker → 内核 (注册 + capability + 预算声明) | FR-007, FR-018, FR-025 |
| `worker-protocol.schema.json` | 内核 ↔ Worker 的 stdio JSON-lines (dispatch / result / heartbeat / abort) | FR-008, FR-014, FR-016 |
| `approval-message.schema.json` | 内核 ↔ 用户审批请求 / 回写 | FR-010, FR-012 |
| `cancel-message.schema.json` | 用户 → 内核 取消指令 | FR-013, FR-015 |
| `audit-event.schema.json` | 内核 → 本地文件系统 审计记录 | FR-019, FR-020 |
| `result-summary.schema.json` | 内核 → 来源通道 终态回推 | FR-029 |

每个 schema 文件配套：
1. JSON Schema (Draft 2020-12) 落 `contracts/*.schema.json`
2. pydantic v2 镜像落 `src/orchestrator_kernel/contracts/*.py`
3. 契约测试落 `tests/contract/test_*.py`（pydantic ↔ JSON round-trip + 反例拒绝）

## Post-Design Gate (Post-Phase 1)

*复核时间：2026-04-21，Phase 1 产出全部落盘后。*

| # | Gate | Status | Delta from Initial | Evidence |
|---|------|--------|--------------------|----------|
| I   | Spec-Driven Development             | ☑ PASS | 无变化。specify→clarify→plan→(Phase 0, 1 完成) 流程线性推进；tasks/analyze 按计划推进 | plan.md §Phase 0/1, research.md R-01..R-10 |
| II  | Least Privilege (Capability Matrix) | ☑ PASS | **强化**：`worker-registration.schema.json` 将 `riskLevel` / `budget` 嵌入每个 capability，注册阶段即硬性校验超硬顶的声明（data-model §E-06） | `contracts/worker-registration.schema.json`, data-model §E-04/E-06 |
| III | Human-in-the-Loop Gate              | ☑ PASS | **强化**：Cancel 消息单独 schema；approval_stale / approval_impersonation_rejected 作为独立审计事件类型，防绕过 | `contracts/approval-message.schema.json`, `contracts/cancel-message.schema.json`, `audit-event.schema.json` eventType enum |
| IV  | Observability & Auditability        | ☑ PASS | **强化**：审计事件枚举总数由模糊变为**36 种 Literal**；敏感字段脱敏由 structlog processor 强制；磁盘写入失败事件独立类型 `disk_write_failed` | `contracts/audit-event.schema.json`, data-model §E-07, R-02/R-06 |
| V   | Sandboxed Execution                 | ☑ PASS | **澄清**：三阶段信号升级（CTRL_BREAK_EVENT → terminate → kill）由 R-08 锁死；stdio `abort` 帧作为**冗余**合作机制，OS 信号为**强制**机制 | research.md R-08, `contracts/worker-protocol.schema.json` AbortFrame |
| VI  | Idempotent & Replayable             | ☑ PASS | **澄清**：幂等缓存重建路径明确走"审计扫描"（R-03），对幂等 replay 的审计事件独立打 `idempotent_replay=true` flag | research.md R-03, `audit-event.schema.json` `idempotent_replay` 字段, INV-1/INV-6 |
| VII | Contract-First                      | ☑ PASS | **落地**：9 份 JSON Schema Draft 2020-12（8 份边界 + 1 份 `budget` 内嵌）+ 对应 pydantic 镜像在 plan.md §Project Structure 中标注路径；契约测试守护 round-trip | `contracts/*.schema.json`, `contracts/README.md`, `tests/contract/` 布局 |
| VIII| Branch-Based Dual-Gate Release      | ☑ PASS | 无变化。所有 Phase 1 产出继续落 `hjx`；仍未向 `main` 合并 | plan.md header, spec.md Feature Branch 字段 |

### 新发现 / 差异说明

1. **契约数量超过 Initial Gate 表预期**：Initial 列出 5 类（FR-024 原文），Phase 1 扩展为 **9 份 schema**（新增 `budget`, `worker-protocol`, `cancel-message`, `result-summary`）。
   - 触发：`cancel-message` 与 `result-summary` 在 Q3/Q4 clarify 后成为一级边界；`worker-protocol` 是 stdio 帧的细化；`budget` 是被多处引用的共享定义。
   - 决策：**不降级为 plan.md 内部细节**，而是保持独立 schema 文件 —— 原因是它们均为跨进程 / 跨通道边界，符合宪法 VII "跨边界" 判定。
   - FR-024 将在 `/speckit-tasks` 或下一个 spec MINOR 版本中同步扩写"5 类 → 9 类"。

2. **审计事件枚举膨胀**：从 spec FR-006 的"状态变更 / 派发 / 结果 / 审批 / 取消 / 崩溃"6 类，实际落到 **36 种 Literal**。
   - 原因：为保证 INV-5（审计可完整重建决策链），必须把每一步决策点独立类型化，避免 `extra` 字段承载语义。
   - 风险：future 新事件类型将触发契约 MINOR 变更 —— 这是**预期成本**，宪法 VII 本就要求契约变更走版本流程。

3. **Windows Job Object 的具体 API 细节延后到 /speckit-tasks**：R-01 已锁定方向，但 `pywin32` vs 手写 ctypes 的选型留到 tasks 拆解时再定；不影响 gate 通过。

### 结论

**Post-Design Gate：8/8 PASS，无需填 Complexity Tracking。**
`/speckit-plan` 阶段结束；下一步进入 `/speckit-tasks`。

## Complexity Tracking

> **当前无违规项，本表空置。**
>
> 若未来设计迭代触发某条宪法 gate 变 FAIL，MUST 在此登记：

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | *(n/a)* | *(n/a)* |
