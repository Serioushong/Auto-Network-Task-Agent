# Phase 1 — Data Model: Orchestrator Kernel MVP

**Feature**: 001-orchestrator-kernel
**Date**: 2026-04-21
**Source**: [spec.md](./spec.md) §Key Entities + §Functional Requirements, [research.md](./research.md)

> 本文档定义 MVP 内核的核心实体、字段、校验规则、状态机与关系。
> 每个实体同时有一个对应的 **契约 schema**（见 [contracts/](./contracts/)），
> 本文档是契约的**语义注解**，schema 是**机器强制**。两处如有冲突以 schema 为准。

---

## 关系总览

```text
EntryEvent (外部入口)
  └──> Trace (1:1) ──> Task Tree (1:N Task, tree structure)
                         │
                         ├── Root Task (用户意图)
                         └── Leaf Task (→ Worker dispatch)
                                  │
                                  └─ dispatched_to ──> Worker (注册表中的实例)
                                                        └── declares ──> Capability[]
                         │
                         └── emits ──> AuditEvent[] (append-only JSONL)

Trace 终态 ──> ResultSummary (经来源通道主动推送给 User)

User ──> ApprovalMessage / CancelMessage (回写，经来源通道)
```

---

## E-01 EntryEvent (入口事件)

**Purpose**: 外部通道投递到内核的单条指令请求。  
**FR Anchors**: FR-001, FR-002, FR-031

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `eventId` | string | ✓ | ULID 或 UUIDv4；`(userId, eventId)` 构成幂等键 | FR-002 |
| `userId` | string | ✓ | 来自通道（OS 用户名 / openId），≤ 128 字符 | FR-012 |
| `text` | string | ✓ | UTF-8 字节数 ≤ **16 384** (16 KB)；超限立即 reject | FR-031, SC-011 |
| `sourceChannel` | enum | ✓ | `cli` \| `http` \| `feishu_stub`（MVP 实装前两项，后者占位） | FR-003 |
| `receivedAt` | string (RFC3339) | ✓ | UTC 时间戳，精度到毫秒 | FR-001 |
| `meta` | object | ✗ | 通道自定义透传字段，审计时按 FR-020 脱敏 | FR-020 |

**Validation rules**:
- 缺任一必需字段 → `rejected(reason=malformed_event)` + 审计落盘（FR-001）
- `text` 超 16 KB → `rejected(reason=payload_too_large, limit=16KB, actual=<bytes>)`（FR-031）
- 重复 `(userId, eventId)` → 不创建新 Trace；返回首次 `traceId` 与当前 / 终态；审计标记 `idempotent_replay=true`（FR-022, FR-023）

---

## E-02 Trace

**Purpose**: 一次用户意图的完整执行上下文，由 `traceId` 聚合该意图下所有 Task 与审计事件。  
**FR Anchors**: FR-004, FR-019, FR-029

| Field | Type | Constraint | FR |
|---|---|---|---|
| `traceId` | string | ULID；内核生成，首次事件落库时创建 | FR-019 |
| `eventId` | string | 原 EntryEvent.eventId，保留便于回推 | FR-029 |
| `userId` | string | 原 EntryEvent.userId；审批 / 回推身份校验依据 | FR-012, FR-029 |
| `sourceChannel` | enum | 原 EntryEvent.sourceChannel；结果汇总回推路径 | FR-029 |
| `createdAt` | string (RFC3339) | 内核创建 trace 的时刻 | FR-019 |
| `rootTaskId` | string | 指向 Task 树根节点 | FR-004 |
| `status` | enum | `pending` \| `running` \| `terminal`（仅三态，细粒度在 Task 上） | — |
| `terminalOutcome` | enum? | 仅 `status=terminal` 时有：`all_succeeded` \| `partial_failed` \| `all_failed` \| `cancelled` \| `denied` \| `rejected` \| `kernel_restarted` | FR-029 |

**Invariants**:
- 一条 EntryEvent ↔ 一个 Trace（除幂等重放）
- `status=terminal` 一经设置不可回退；`terminalOutcome` 与其同时落盘；触发 FR-029 结果汇总推送

---

## E-03 Task

**Purpose**: 任务树节点，根节点代表用户意图，叶节点代表发给某 Worker 的动作请求。  
**FR Anchors**: FR-004, FR-005, FR-008, FR-010, FR-017, FR-018, FR-028

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `taskId` | string | ✓ | ULID；稳定 | FR-004 |
| `parentTaskId` | string? | 非根必填 | ULID；根 Task 为 null | FR-004 |
| `traceId` | string | ✓ | 所属 Trace | FR-019 |
| `kind` | enum | ✓ | `root_intent` \| `leaf_action` | FR-004 |
| `capability` | string? | 叶节点必填 | 与 Worker 注册表匹配 | FR-008 |
| `riskLevel` | enum? | 叶节点必填 | `NORMAL` \| `HIGH_RISK` | FR-025 |
| `budget` | Budget | 叶节点必填 | 见 E-04 | FR-018 |
| `state` | enum | ✓ | 见下方"状态机" | FR-005 |
| `outcome` | enum? | 仅终态有 | `succeeded` \| `failed` \| `cancelled` \| `denied` \| `denied_by_timeout` | FR-005 |
| `failureReason` | enum? | 仅 failed / denied 有 | 见下方"失败原因枚举" | FR-008, FR-017, FR-018, FR-028 |
| `payload` | object | ✓ | dispatch 时传给 Worker 的数据（按 FR-020 脱敏后入审计） | FR-024 |
| `resultHash` | string? | 仅 succeeded 有 | Worker 返回结果的 SHA-256 前 16 bytes hex | FR-019 |
| `assignedWorkerId` | string? | 叶节点 dispatched 后填 | Worker 注册表主键 | FR-007 |
| `createdAt` / `dispatchedAt` / `startedAt` / `terminalAt` | string (RFC3339)? | 按状态填 | 审计事件源 | FR-019 |

### 状态机 (Task.state)

```text
               ┌──────────────────────────────────────┐
               │                                      v
 pending ──▶ pending_approval ──▶ dispatched ──▶ running ──┐
    │             │                   │             │      ├─▶ succeeded
    │             │                   │             │      ├─▶ failed
    │             │                   ▼             │      ├─▶ cancelled
    │             │              cancelled          │      └─▶ (timeout) failed
    │             ├─▶ denied (user_rejected)        │
    │             └─▶ denied (approval_timeout)     │
    │                 (= denied_by_timeout)         │
    │                                               │
    └─▶ failed (no_capable_worker / malformed)     │
                                                    │
  任意非终态 ──(kernel restart)──▶ failed(kernel_restart) ◀─ FR-028
```

**单向约束 (FR-005)**:
- `succeeded / failed / cancelled / denied / denied_by_timeout` 均为终态；一旦进入 MUST NOT 再变更。
- `pending_approval` 仅存在于 HIGH_RISK 叶 Task（FR-010）；NORMAL 叶 Task 跳过此节点。
- 内核重启补写 `failed(reason=kernel_restart)` **不违反**单向约束 —— 非终态 → 终态仍是合法单向转换（FR-028）。

### 失败原因枚举 (`failureReason`)

| Value | 触发点 | FR |
|---|---|---|
| `no_capable_worker` | 无 Worker 声明该 capability | FR-008 |
| `sandbox_limit` | 资源上限触发（内存 / CPU / wall） | FR-017 |
| `budget_exceeded` | 预算三维超限，附带 `dim=wall\|tool\|token` | FR-018 |
| `worker_crashed` | Worker 异常退出 / 抛未捕获异常 | SC-005, P5 |
| `hard_terminated` | 软中止 3 秒未响应升级硬终止 | FR-014 |
| `kernel_restart` | FR-028 启动扫描补终态 | FR-028 |
| `user_rejected` | 审批流用户明确 deny | FR-012 |
| `approval_timeout` | 审批超时（= `denied_by_timeout`） | FR-011 |
| `rate_limited` | 仅 rejection_event 层，不落到 Task（Task 未创建） | FR-027 |
| `malformed_event` | 同上 | FR-001 |
| `payload_too_large` | 同上 | FR-031 |
| `kernel_warming_up` | FR-028 扫描期间被拒 | FR-028 |

---

## E-04 Budget (三维预算)

**Purpose**: Worker 注册时为每个 capability 声明的执行预算；运行期监控超限即终止。  
**FR Anchors**: FR-018

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `wall_clock_ms` | int | ✓ | 1 ≤ v ≤ **1 800 000** (30 min 系统硬顶) | FR-018 |
| `max_tool_calls` | int | ✓ | 1 ≤ v ≤ **200** (系统硬顶) | FR-018 |
| `max_tokens` | int | ✓ | 1 ≤ v ≤ **500 000** (系统硬顶) | FR-018 |

**Default fallback (当 Worker 未声明)**:
- `wall_clock_ms=60_000`, `max_tool_calls=10`, `max_tokens=20_000`

**System hard cap rule**:
- 任一维度声明值 > 硬顶 → Worker 注册阶段 `rejected(reason=budget_exceeds_system_cap)` 并记审计；Worker 未注册成功，其 capability 不进分派候选集。

---

## E-05 Worker (Worker 注册条目)

**Purpose**: 注册到内核的执行单元；MVP 阶段为独立子进程形式的 stub。  
**FR Anchors**: FR-007, FR-009, FR-016, FR-017, FR-018

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `workerId` | string | ✓ | ULID 或 Worker 自选的可读名（例 `echo-stub-1`） | FR-007 |
| `pid` | int | ✓ | 启动时写入；崩溃后不清空（审计可查） | — |
| `capabilities` | Capability[] | ✓ | 至少一项；见 E-06 | FR-007, FR-025 |
| `resourceLimits` | ResourceLimits | ✓ | `memory_mb`, `cpu_pct`, `wall_clock_ms`（Worker 进程级别，覆盖单 Task 的 budget 时取 min） | FR-017 |
| `health` | enum | ✓ | `healthy` \| `unhealthy` | FR-009 |
| `missedHeartbeats` | int | ✓ | 累计缺失计数；`≥ 3` 触发 `unhealthy`（可配置） | FR-009 |
| `lastHeartbeatAt` | string (RFC3339) | ✓ | 最近心跳到达时间 | FR-009 |

**Lifecycle states**: `starting → healthy ⇄ unhealthy → terminated`
- `unhealthy` Worker MUST NOT 被选作新分派目标（FR-008/009）
- 其上已 running Task 维持原状直至 Worker 恢复 或 超时 / 被取消

---

## E-06 Capability

**Purpose**: Worker 可执行的原子能力命名；分派时按名匹配。  
**FR Anchors**: FR-007, FR-008, FR-018, FR-025

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `name` | string | ✓ | 点分命名，例 `echo.say` / `file.delete`；≤ 64 字符 | FR-007 |
| `riskLevel` | enum | ✓ | `NORMAL` \| `HIGH_RISK` | FR-025 |
| `budget` | Budget | ✓ | 见 E-04；Worker 为该 capability 声明的执行预算 | FR-018 |
| `description` | string? | ✗ | 人类可读说明，入审计便于回溯 | — |

---

## E-07 AuditEvent

**Purpose**: 系统唯一事实来源；任一状态变更、派发、审批、取消、崩溃、通知等均 append 一条。  
**FR Anchors**: FR-006, FR-019, FR-020, FR-021, FR-023, FR-028

| Field | Type | Required | Constraint | FR |
|---|---|---|---|---|
| `auditId` | string | ✓ | ULID，内核生成（用于内部回放游标） | FR-019 |
| `timestamp` | string (RFC3339) | ✓ | UTC，毫秒精度 | FR-019 |
| `traceId` | string? | ✗ | 某些事件如 `rate_limited_rejection` 在 Task 创建前，允许 null | FR-027 |
| `taskId` | string? | ✗ | 同上 | FR-019 |
| `parentTaskId` | string? | ✗ | 便于树回溯 | FR-019 |
| `actor` | enum | ✓ | `kernel` \| `worker:<id>` \| `user:<id>` \| `system` | FR-019 |
| `capability` | string? | ✗ | 叶动作相关事件有 | FR-019 |
| `eventType` | enum | ✓ | 见下方"事件类型枚举" | FR-006 |
| `input_hash` | string? | ✗ | SHA-256 前 16 bytes hex；脱敏输入 | FR-020 |
| `output_hash` | string? | ✗ | 同上 | FR-020 |
| `outcome` | enum? | ✗ | 终态事件有 | FR-005 |
| `extra` | object | ✗ | 事件专属扩展字段；须事先脱敏 | FR-020 |

### 事件类型枚举 (`eventType`)

> 下表是 MVP 所需**全部**事件类型。实现 MUST 以 `Literal[...]` 锁死枚举，出现未列类型即视为契约违背。

| eventType | 触发时机 | actor |
|---|---|---|
| `event_received` | 入口事件通过 schema 校验 | `kernel` |
| `event_rejected_malformed` | schema 校验失败 | `kernel` |
| `event_rejected_too_large` | text > 16 KB | `kernel` |
| `event_rejected_rate_limited` | FR-026/027 限流 | `kernel` |
| `event_rejected_warming_up` | FR-028 启动扫描期间 | `kernel` |
| `trace_created` | 创建 Trace + root Task | `kernel` |
| `idempotent_replay` | 重复投递命中幂等缓存 | `kernel` |
| `task_created` | 叶 Task 构建入树 | `kernel` |
| `task_pending_approval` | HIGH_RISK 进入审批门 | `kernel` |
| `approval_granted` | 用户 approve | `user:*` |
| `approval_denied` | 用户 deny | `user:*` |
| `approval_timeout` | 10 分钟（FR-011）未响应 | `kernel` |
| `task_dispatched` | 选中 Worker 并发出 dispatch | `kernel` |
| `task_started` | Worker 回传 started ack | `worker:*` |
| `task_succeeded` | Worker 回传 succeeded | `worker:*` |
| `task_failed` | 各类失败（reason 见 failureReason 枚举） | `kernel` \| `worker:*` |
| `cancel_requested` | 用户 cancel 到达 | `user:*` |
| `soft_abort_sent` | 内核发 CTRL_BREAK_EVENT | `kernel` |
| `hard_abort_sent` | 3s 升级 terminate/kill | `kernel` |
| `task_cancelled` | Task 终态落盘为 cancelled | `kernel` |
| `worker_registered` | Worker 注册成功 | `kernel` |
| `worker_heartbeat` | Worker 心跳（可降采样或按 miss 才记） | `worker:*` |
| `worker_unhealthy` | 连续 N 次 miss | `kernel` |
| `worker_recovered` | 从 unhealthy 回归 | `kernel` |
| `worker_terminated` | Worker 退出（正常或异常） | `kernel` |
| `budget_exceeded` | 运行期预算超限 | `kernel` |
| `sandbox_limit_hit` | Job Object / psutil 检测到超限 | `kernel` |
| `kernel_restart_detected` | FR-028 启动扫描开始 | `kernel` |
| `in_flight_auto_failed` | 扫描为未终态 Task 补写终态 | `kernel` |
| `result_summary_prepared` | 生成 ResultSummary | `kernel` |
| `result_summary_delivered` | 来源通道成功回执 | `kernel` |
| `result_summary_retrying` | 某次投递失败，进入下一轮退避 | `kernel` |
| `notification_delivery_failed` | 3 次重试后仍失败 | `kernel` |
| `disk_write_failed` | 审计写入失败，触发拒绝新入口状态 | `kernel` |

**Retention**: 按 FR-021 物理保留 ≥ 30 天；rotation 粒度 1 天。

**Determinism**: 对同一 traceId，审计事件按 `timestamp` + `auditId` 排序 MUST 可完整回放 Trace 的决策链（FR-019）。

---

## E-08 ApprovalMessage

**Purpose**: 内核 ↔ 用户之间的审批请求与回写。  
**FR Anchors**: FR-010, FR-011, FR-012

### Outbound (kernel → user, via sourceChannel)

| Field | Type | Constraint |
|---|---|---|
| `kind` | literal | `approval_request` |
| `traceId` | string | 必填 |
| `taskId` | string | 叶 Task |
| `capability` | string | 叶 capability |
| `riskLevel` | literal | `HIGH_RISK` |
| `summary` | string | 人读摘要（已脱敏） |
| `expiresAt` | string (RFC3339) | 默认 now + 10 min（FR-011） |

### Inbound (user → kernel, via sourceChannel)

| Field | Type | Constraint |
|---|---|---|
| `kind` | literal | `approval_response` |
| `traceId` | string | 必填 |
| `decision` | enum | `approve` \| `deny` |
| `userId` | string | 必须与 Trace.userId 相同，否则拒绝（FR-012） |
| `receivedAt` | string (RFC3339) | 内核接收时刻 |

**Validation**:
- `userId` mismatch → `audit_event: approval_impersonation_rejected`，不改 Task 状态
- `decision=approve` 且 Task.state ≠ `pending_approval` → `audit_event: approval_stale`，不改状态（已 cancel / 已超时场景）
- 竞态：`approve` 与 `cancel` 并发，**按到达顺序**处理最后到达的指令（spec Edge Case 已定）

---

## E-09 CancelMessage

**Purpose**: 用户 → 内核 的取消指令。  
**FR Anchors**: FR-013, FR-014, FR-015

| Field | Type | Constraint |
|---|---|---|
| `kind` | literal | `cancel_request` |
| `traceId` | string | 必填 |
| `userId` | string | 身份校验，同 Approval |
| `receivedAt` | string (RFC3339) | 内核接收时刻 |

**Outcomes**:
- trace 不存在 → 返回 `not_found`，审计 `cancel_not_found`（FR-015）
- trace 全部 Task 均终态 → 返回 `already_terminal`，审计 `cancel_late`
- 否则按 FR-013/014 执行软→硬中止升级，≤ 5 秒内令所有 running/dispatched/pending_approval Task 进入 `cancelled`

---

## E-10 ResultSummary

**Purpose**: 内核主动经来源通道推送给发号者的 Trace 终态汇总。  
**FR Anchors**: FR-029, FR-030, SC-010

| Field | Type | Constraint |
|---|---|---|
| `kind` | literal | `result_summary` |
| `traceId` | string | 必填 |
| `eventId` | string | 原 EntryEvent.eventId |
| `userId` | string | 原 EntryEvent.userId |
| `commandDigest` | string | 原 text 经 FR-020 脱敏后的一行摘要（≤ 256 字符） |
| `traceOutcome` | enum | `all_succeeded` \| `partial_failed` \| `all_failed` \| `cancelled` \| `denied` \| `rejected` \| `kernel_restarted` |
| `leafResults` | LeafResult[] | 每个叶 Task 一条 |
| `message` | string | 人读结论（含 kernel_restart 特别提示） |
| `preparedAt` | string (RFC3339) | 内核生成时刻 |
| `deliveryAttempt` | int | 0=首次；重试时递增 |

### LeafResult (内嵌)

| Field | Type | Constraint |
|---|---|---|
| `taskId` | string | 必填 |
| `capability` | string | 必填 |
| `outcome` | enum | Task.outcome |
| `failureReason` | string? | 如有 |
| `resultHash` | string? | 如 succeeded |

**Delivery contract**:
- 首次投递在 Trace 终态落盘后立刻执行
- 失败按退避 0s/1s/4s/16s 重试，最多 3 次
- 投递成功 → `audit_event: result_summary_delivered`
- 全失败 → `audit_event: notification_delivery_failed`，MUST NOT 回滚 Trace 状态
- 敏感字段（commandDigest / message）MUST 在生成时已完成脱敏；contract schema 不再关心原文

---

## 关键不变量 (Global Invariants)

| ID | 不变量 | Enforced by |
|---|---|---|
| INV-1 | 每条 EntryEvent 在内核视角下有且只有一个 Trace | idempotency cache + audit scanner |
| INV-2 | Task.state 单向转换 | state_machine.py + property tests (hypothesis) |
| INV-3 | HIGH_RISK 叶 Task MUST 经过 `pending_approval` 才能 `dispatched` | approval_gate.py + integration test |
| INV-4 | 非终态 Task 在内核进程存活期间 MUST 持续出现在内存 `runtime_tasks` 集合中 | scheduler invariant |
| INV-5 | 审计日志的 `(traceId, auditId)` 序列 MUST 允许独立重建 Trace 决策链 | contract test + scanner roundtrip |
| INV-6 | 未终态 Task 必在下次内核启动时被强制转为 `failed(kernel_restart)` | FR-028 scanner |
| INV-7 | 每个 Trace 终态 MUST 对应 ≥ 1 次 `result_summary_delivered` OR 1 次 `notification_delivery_failed` 审计记录 | notifier + audit invariant test |
| INV-8 | 敏感字段 MUST NOT 以明文出现在审计 JSONL 任何一行 | redact processor + property test (随机样本 grep 断言) |

---

## 与契约 schema 的映射

| 实体 | Schema 文件 | Pydantic 模块 |
|---|---|---|
| E-01 EntryEvent | `contracts/entry-event.schema.json` | `src/orchestrator_kernel/contracts/entry_event.py` |
| E-02 Trace | （内嵌入 task / audit，不单独暴露外部）| `src/orchestrator_kernel/contracts/trace.py`（内部） |
| E-03 Task | `contracts/task.schema.json` | `contracts/task.py` |
| E-04 Budget | （嵌入 worker-registration 与 task） | `contracts/budget.py` |
| E-05 Worker | `contracts/worker-registration.schema.json` | `contracts/worker.py` |
| E-06 Capability | （嵌入 worker-registration） | 同上 |
| E-07 AuditEvent | `contracts/audit-event.schema.json` | `contracts/audit.py` |
| E-08 ApprovalMessage | `contracts/approval-message.schema.json` | `contracts/approval.py` |
| E-09 CancelMessage | `contracts/cancel-message.schema.json` | `contracts/cancel.py` |
| E-10 ResultSummary | `contracts/result-summary.schema.json` | `contracts/result_summary.py` |
| — | `contracts/worker-protocol.schema.json` (stdio JSON-lines 帧) | `contracts/worker_protocol.py` |
