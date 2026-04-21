# Phase 0 — Research: Orchestrator Kernel MVP

**Feature**: 001-orchestrator-kernel
**Date**: 2026-04-21
**Input**: [plan.md](./plan.md) §Technical Context + §Phase 0

> 本文档记录 MVP 阶段每项关键技术选型的 Decision / Rationale / Alternatives considered。
> Spec 阶段已关闭全部 5 项 `[NEEDS CLARIFICATION]`；本阶段无"未知变量"，
> 全部为"多方案中选一"的决策固化。

---

## R-01 Worker 隔离机制

- **Decision**: `subprocess.Popen` + Windows **Job Object**（内存 / CPU / 墙钟软性限制）+ `psutil` 监控；软中止先发 `CTRL_BREAK_EVENT` (Windows) / `SIGTERM` (POSIX)，3 秒内未退出升级为 `terminate()` / `kill()`。
- **Rationale**:
  1. 宪法 Additional Constraints 明确要求 Windows 10 + PowerShell 5.1 可完整运行，Docker / Firecracker 引入额外运行时依赖，违反"最省部署复杂度"。
  2. MVP Worker 为 stub，不跑用户代码，无需强隔离（恶意代码防御列为后续 feature 的话题）。
  3. Windows Job Object 原生支持子进程树绑定 + 内存上限 + 进程数限制，psutil 补齐跨平台 CPU / wall-clock 采样。
  4. stdio JSON-lines 协议与 subprocess 天然契合，避免额外消息中间件。
- **Alternatives considered**:
  - *Docker containers*：部署复杂，Windows Home 无 Hyper-V 时不可用；资源限制更强但 MVP 不需要。作为"后续 feature 强化隔离"的升级路径预留。
  - *Firecracker / gVisor*：Linux-only，直接违反 Windows 一级支持。
  - *WASM runtimes (wasmtime)*：要求把 Worker 重写为 WASM 模块，与"Worker 即独立进程"的语义不一致；MVP 不引入。
  - *Python multiprocessing*：GIL 释放但崩溃隔离弱于 subprocess；父子共享内存带来安全风险。

## R-02 审计日志存储

- **Decision**: 本地文件系统 **JSONL**，路径 `./var/audit/YYYY-MM-DD.jsonl`，按 UTC 日切 rotation；老日志通过文件系统保留策略 ≥ 30 天（FR-021）。写入失败（磁盘满 / 权限拒绝）立即切入"拒绝新入口事件"状态（SC-008）。
- **Rationale**:
  1. JSONL 是最简单的"append-only + 可 grep + 可 tail"事实日志格式，契合 FR-028 "审计为事实来源"。
  2. FR-028 启动扫描只需顺序读最近一个日期文件，无需索引 → 无 DB 必要。
  3. 文件系统自带原子 append 语义（`O_APPEND` 对 ≤ 4 KB 单行写入在 POSIX / NTFS 均原子），简化并发写。
  4. 部署零依赖，便于 Windows PowerShell 下演示。
- **Alternatives considered**:
  - *SQLite*：查询灵活但引入 schema migration 成本；FR-028 扫描语义用 JSONL 更直接。列为"审计分析层"后续增强。
  - *外部日志服务（ELK / Loki）*：生产级但 MVP 单机过度；违反宪法"MVP 最省部署"。
  - *Binary format (protobuf + length-prefix)*：性能更好但丢失 grep / tail 的运维便利；MVP 不追求极限吞吐。

## R-03 幂等键存储

- **Decision**: 内核进程内 `dict[(userId, eventId)] → traceId` + 每条幂等命中时追加审计事件（`idempotent_replay=true`）；内核冷启动时按 FR-028 从当日审计 JSONL 扫描重建（先扫 `event_received`，再扫终态，最后剩余未终态的立即补 `failed(reason=kernel_restart)` + 结果汇总推送）。
- **Rationale**:
  1. "内存为工作态、审计为事实来源" 是 FR-028 的直接产物。
  2. MVP 单机单进程，不涉及多节点共享状态。
  3. 重建成本与"当日审计条数 × O(1)"成正比，按 FR-026 峰值 50 events/sec × 86400 s ≈ 4.3M 条，顺序解析 JSONL 在现代磁盘 < 30 秒，满足 SC-009 "≤ 10 秒"（实际压测时只需扫最近活跃区间，可早停）。
- **Alternatives considered**:
  - *Redis*：外部依赖，违反"单机零中间件"目标。
  - *SQLite WAL*：写性能 OK 但与 JSONL 审计并行两份事实来源易脱同步；违反单一事实源。
  - *嵌入 LMDB*：极快但 Python 生态薄；非必要复杂度。

## R-04 契约 schema 工具

- **Decision**: **pydantic v2** 作为运行时 model + 使用 `model_json_schema()` 导出 **JSON Schema Draft 2020-12** 落 `contracts/*.schema.json`。两边 round-trip 由 `tests/contract/` 守护。
- **Rationale**:
  1. 宪法 VII 原文明列 "JSON Schema 或 Protobuf 或 Pydantic/Zod"，pydantic 是 Python 生态首选。
  2. pydantic v2 的 `RootModel` / `Discriminator` / `Literal` 足以覆盖本 MVP 所有 union 类型（Task outcome, Worker message variant, Rejection reason 等）。
  3. JSON Schema 同时产出给将来的非 Python 客户端（例如飞书入口适配器可能用 Go）—— 契约可跨语言复用。
  4. `pydantic-settings` 可复用于配置装载，减少依赖数。
- **Alternatives considered**:
  - *JSON Schema 手写 + jsonschema 运行时校验*：两层定义易脱同步；失去静态类型。
  - *Protobuf / gRPC*：过重，MVP 不跨语言；`.proto` 生成器引入额外构建步骤。
  - *Zod (TS 世界)*：语言不匹配。
  - *attrs + cattrs*：轻但 JSON Schema 导出生态弱于 pydantic。

## R-05 CLI 框架

- **Decision**: **typer** (0.12+)。
- **Rationale**:
  1. 基于 click，API 现代，类型注解即参数声明，与 pydantic 语义无缝。
  2. 自动生成 `--help` 和 shell completion，降低 Alpha 用户上手成本（SC-001 ≤ 3 min）。
  3. 单文件 entry point 足以覆盖 MVP 的 3~5 个子命令（submit / approve / deny / cancel / status）。
- **Alternatives considered**:
  - *click*：typer 底层就是它，但 typer 的类型推断更省样板。
  - *argparse*：stdlib 但无自动 help 美化、无 completion。
  - *fire*：API 太魔法，不适合需要严格 schema 的场景。

## R-06 结构化日志库

- **Decision**: **structlog** (24.x)。
- **Rationale**:
  1. 原生面向 JSONL 输出，`BoundLogger` 支持把 `traceId` / `taskId` / `actor` 等以 context manager 形式自动注入（FR-019 所有字段硬要求）。
  2. 与 stdlib logging 互操作，subprocess Worker 输出的 stderr 可无损汇入同一审计流。
  3. 处理器链式组合，便于把 "敏感字段脱敏" 作为独立 processor 插入（FR-020）。
- **Alternatives considered**:
  - *logging + python-json-logger*：可行但 context 注入需要手工维护，容易漏字段。
  - *loguru*：API 糖更多但生态侵入性强，不易嵌入自定义 processor。

## R-07 属性测试 / 幂等验证

- **Decision**: **hypothesis** (6.x) + 手写 state-machine 策略 for FR-002 / FR-022 幂等性与 FR-005 Task 状态机单向转换。
- **Rationale**:
  1. 幂等性是"对任意输入序列，重复投递等价于单次投递"，属于典型的代数属性，hypothesis 的 `RuleBasedStateMachine` 能高覆盖率地生成投递序列。
  2. Task 状态机的"单向"约束也是集合级不变量（`visited_states` 的前缀序关系），与 hypothesis 匹配良好。
  3. 宪法 Article VIII 要求 TDD 红-绿-重构，hypothesis 能让"红"阶段的失败案例非常有诊断价值。
- **Alternatives considered**:
  - *手写 fuzz loop*：覆盖率不如 hypothesis 的 shrinking 策略。
  - *schemathesis*：偏 HTTP API 模糊测试，与本 MVP 的内部状态机场景不贴合。

## R-08 Windows 软中止信号

- **Decision**: 软中止阶段向 Worker subprocess 发 `signal.CTRL_BREAK_EVENT` （通过 `os.kill(pid, signal.CTRL_BREAK_EVENT)` 或 `Popen.send_signal()`；subprocess 创建时 MUST 带 `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`）；3 秒内不退出 → `Popen.terminate()` → 再 1 秒 → `Popen.kill()`。POSIX 下对应 `SIGTERM` → `SIGKILL`。
- **Rationale**:
  1. `CTRL_BREAK_EVENT` 是 Windows 唯一能让 Python 子进程通过 `signal.signal(signal.SIGBREAK, ...)` 捕获并执行优雅清理的信号 —— 符合宪法 V "优先清理当前操作而非强制 kill"。
  2. `CREATE_NEW_PROCESS_GROUP` 确保信号只传给目标 Worker 而不是整个内核进程组（否则会反伤内核本身）。
  3. 三阶段升级（SIGBREAK → terminate → kill）对应 FR-014 的"3 秒内未响应升级为硬终止"。
- **Alternatives considered**:
  - *taskkill /T /F*：外部命令，Windows-only，排错不便；失去与 POSIX 的语义对称。
  - *仅 terminate / kill*：失去"软"中止阶段，违反 Constitution V。
  - *通过 stdio 发 JSON "abort" 消息*：Worker 若卡住无法消费 stdin，则命令永远不生效；stdio 作为 **冗余通知**（让 well-behaved Worker 知道要优雅退出）而非**唯一机制**。最终两者并用：stdio `abort` + OS 信号。

## R-09 来源通道回推与重试

- **Decision**: FR-029 结果汇总消息推送采用 **指数退避 3 次重试**：首次立即 → 1 s → 4 s → 16 s；总预算约 22 s。3 次全失败 MUST 落审计 `notification_delivery_failed`，但 MUST NOT 改写 Task 终态（Task 终态是审计事实，通知只是用户体验层）。
- **Rationale**:
  1. SC-010 要求"首次 ≥ 99% / 重试后 100%"；指数退避对短时通道抖动覆盖良好。
  2. 22 s 总预算内可让 Task 终态仍对用户 "感觉即时"（比 SC-002 的 3 秒略长但可接受，因终态推送非关键路径）。
  3. 审计记录失败允许运维事后人工通告，避免系统状态被通知层绑架。
- **Alternatives considered**:
  - *无限重试*：阻塞资源且放大故障扇出；被拒。
  - *单次失败即 fail-silent*：违反 SC-010 "重试后 100%"。
  - *死信队列 + 异步调度*：MVP 单机无消息队列，过度设计。

## R-10 LLM 抽象层最小形态

- **Decision**: 在 `src/orchestrator_kernel/llm/client.py` 定义 `LLMClient` 为 `typing.Protocol`，方法签名 `async def complete(prompt: str, *, max_tokens: int, budget: TokenBudget) -> LLMResponse`。MVP 不提供任何具体实现；Worker stub 只调用本地 deterministic 逻辑。后续 feature 引入真实 LLM 时直接注册实现即可。
- **Rationale**:
  1. 宪法 Additional Constraints 明确禁止"硬编码任一厂商 SDK"。
  2. `Protocol` 是 Python 的 structural typing，不强制继承 ABC，允许将来用 `litellm`、`instructor`、`openai` 等任一实现直接 duck-typing 适配。
  3. MVP 不真调 LLM 避免在"内核语义验证"阶段引入外部 cost / rate limit / 非确定性，保障 TDD 的可重复性。
- **Alternatives considered**:
  - *抽象基类 (ABC)*：强制继承，增加将来替换成本。
  - *直接引用 `litellm`*：虽然 litellm 本身已是厂商抽象层，但它仍是一个外部运行时依赖，违反 MVP "验内核语义" 目标。
  - *不留任何抽象点*：后续引入 LLM 时将需要侵入式修改，违反"契约先行"精神。

---

## Cross-reference

| Research ID | Feeds into (Design artifact) | Locked constraint |
|---|---|---|
| R-01 | `worker_supervisor/`, FR-014/016/017, SC-005 | subprocess + Job Object + 3 级信号升级 |
| R-02 | `audit/writer.py`, FR-019/021, SC-006/008 | JSONL by-day rotation, append-only |
| R-03 | `kernel/idempotency.py`, `audit/scanner.py`, FR-022/028, SC-003/009 | memory + audit-rebuild, no external KV |
| R-04 | `contracts/*.schema.json`, `src/.../contracts/*.py`, FR-024 | pydantic v2 + JSON Schema Draft 2020-12 |
| R-05 | `entrypoints/cli.py`, SC-001 | typer |
| R-06 | `audit/writer.py`, `kernel/*`, FR-019 | structlog |
| R-07 | `tests/integration/test_p2_idempotency.py`, `tests/unit/test_state_machine.py` | hypothesis RuleBasedStateMachine |
| R-08 | `kernel/cancel.py`, `worker_supervisor/lifecycle.py`, FR-014 | CTRL_BREAK_EVENT → terminate → kill |
| R-09 | `notifier/delivery.py`, FR-029, SC-010 | 0s / 1s / 4s / 16s, audit on final fail |
| R-10 | `llm/client.py` | Protocol, no vendor SDK in MVP |

---

## Open follow-ups (non-blocking)

> 以下项不影响 MVP 落地，但应在后续 feature 之前再开一次 clarify：
>
> - *Worker 进程内存监控轮询间隔*（psutil 采样频率 vs CPU 开销）—— 预设 500 ms，若 SC-005 / SC-011 压测出现尾延迟再优化。
> - *审计日志 rotation 的归档策略*（压缩 / 外传）—— 预设仅按 30 天物理保留，符合 FR-021，归档延后。
> - *HIGH_RISK capability 清单*（按 Assumptions 第 7 条延后）—— 将在第一个引入真实桌面操控 Worker 的 feature 中定义。
