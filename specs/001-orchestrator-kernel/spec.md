# Feature Specification: Orchestrator Kernel (主 Agent 编排内核 MVP)

**Feature Branch**: `hjx` (per Constitution Article VIII: no per-feature branch)
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: "主 Agent 编排内核 MVP——构建多 Agent 自动化协同办公系统的大脑。接收外部入口投递的自然语言指令，转化为 Task 树，分派给具备相应能力的子 Agent，全过程维护任务生命周期、执行隔离、人工审批、审计日志、幂等性、可取消性。本阶段仅实现内核本身，Worker Agent 与飞书入口均以 stub 形式存在。"

## Clarifications

### Session 2026-04-21

- Q: HIGH_RISK Task 的人工审批超时默认值？ → A: 10 分钟（Option A，风险暴露最短；容忍短时离席，超过则判定为 denied_by_timeout）
- Q: 每任务的预算上限（wall-clock / tool-call / token）如何设定？ → A: 按 capability 自声明（Option D）。Worker 注册时 MUST 为每个 capability 声明 `{wall_clock_ms, max_tool_calls, max_tokens}`；未声明则 fallback 到保守默认（wall 60s / 10 calls / 20k tokens）；内核另设系统硬顶（wall 30min / tool-call 200 / token 500k），任何声明值不得超越硬顶
- Q: 入口事件速率限制与每用户并发上限？ → A: 按 capability 风险分级（Option C）。普通 capability 每用户并发活跃 trace ≤ 10；HIGH_RISK capability 每用户并发未决 / 运行中 Task ≤ 1（防审批洪水）；全局入口峰值 ≤ 50 events/sec；每用户入口 ≤ 120 events/min。超限回 `rejected(reason=rate_limited)`，入审计，不入 Task 队列
- Q: 内核崩溃 / 重启后 in-flight Task 的恢复语义？ → A: Fail-fast（Option A）+ 结果回推。内核重启后所有 in-flight Task（`pending | dispatched | running | pending_approval`）MUST 被置为 `failed(reason=kernel_restart)`；内核状态纯内存，无需持久化 Task 状态；审计日志（磁盘）为事实来源。**追加约束**：每个 Task 的终态（succeeded / failed / cancelled / denied / denied_by_timeout）MUST 由内核主动经来源通道回推给命令发号者，并附本次 trace 的结果汇总（按 traceId 聚合所有叶 Task 的 outcome），用户不得依赖轮询
- Q: 入口事件 payload 大小上限？ → A: text ≤ 16 KB（Option A，严格但安全）。超限 MUST 立即拒绝 `rejected(reason=payload_too_large, limit=16KB)`，不入 Task 队列，不做截断；阈值在部署配置可覆写，但覆写值 MUST ≤ 系统硬顶 1 MB

## User Scenarios & Testing *(mandatory)*

### User Story 1 — 基本分派闭环 (Priority: P1)

Alpha 用户（开发者 / 运维）通过 CLI 投递一条自然语言指令（例："帮我把桌面上的 `notes.txt` 备份到 D 盘"）。
编排内核收到事件后，将其转化为一棵 Task 树（1 个根意图 → 若干叶动作），按声明的 capability 派发给对应 Worker stub；每个 stub 返回一个模拟结果；
内核汇总 Task 树最终状态，向来源通道回写结果，并在本地审计日志里留下完整轨迹。

**Why this priority**: 这是整个系统最小闭环，没有它谈不上其他任何高级行为。P1 即构成可演示的 MVP。

**Independent Test**: 可以只实现 P1：准备一个 echo-stub Worker（声明 capability `echo.say`），
提交一条映射到 `echo.say` 的指令，验证 (a) Task 树被正确创建，(b) 派发到 stub 并拿到结果，(c) 来源通道收到最终回复，(d) 审计日志出现从入口事件到结果的完整链路。

**Acceptance Scenarios**:

1. **Given** 内核已启动且 stub Worker `echo-worker` 已注册并声明 `echo.say`，
   **When** Alpha 用户通过 CLI 提交事件 `{eventId: E1, user: alice, text: "echo hello"}`，
   **Then** 内核在 ≤ 3 秒内向 CLI 回写 `{traceId, root.status: succeeded, leaves: 1, output: "hello"}`，且审计日志中出现 E1 对应的完整事件链。
2. **Given** 没有任何 Worker 声明所需 capability，
   **When** Alpha 用户提交一条映射到 `desktop.click` 的指令，
   **Then** 内核以 `failed(reason=no_capable_worker)` 关闭该 Task 并回写友好错误，审计日志记录该决策。
3. **Given** 事件 payload 缺字段（如无 eventId），
   **When** 入口收到该 payload，
   **Then** 内核拒绝事件并返回 `rejected(reason=malformed_event)`，不创建任何 Task。

---

### User Story 2 — 幂等性保证 (Priority: P2)

Alpha 用户重复投递同一事件（同一 eventId）5 次。编排内核必须仅在首次投递时创建 Task 树并执行，后续 4 次返回首次结果或当前执行状态，不产生任何重复副作用。

**Why this priority**: 飞书消息重投、用户双击、崩溃恢复等场景都会触发重复投递；没有幂等保证，副作用会被放大。P2 是系统可信度的底线。

**Independent Test**: 实现 P2 后，可通过脚本在 1 秒内向入口发送 5 次同 eventId 的指令，验证 (a) 审计日志只出现 1 棵 Task 树，(b) stub Worker 只被调用 1 次，(c) 5 次 CLI 请求都收到同一 traceId 与结果（或当前状态），(d) 所有 4 次重复请求被标记 `idempotent_replay=true`。

**Acceptance Scenarios**:

1. **Given** 事件 `E2` 已被内核成功处理并完成，
   **When** Alpha 用户再次以 eventId=E2 提交同样 payload，
   **Then** 内核直接返回首次的 traceId 与 final result，且审计日志新增一条 `idempotent_replay` 事件但不创建新 Task。
2. **Given** 事件 `E3` 的 Task 树正在 running 状态，
   **When** Alpha 用户在 running 期间以 eventId=E3 再次投递，
   **Then** 内核返回当前中间状态（包含 traceId、进度），不重新分派，不复制 Task 树。
3. **Given** 事件 `E4` 因 `failed(reason=no_capable_worker)` 已关闭，
   **When** Alpha 用户以 eventId=E4 再次投递，
   **Then** 内核按幂等语义返回首次的失败结果；若用户希望重试必须使用新 eventId。

---

### User Story 3 — HIGH_RISK 人工审批门 (Priority: P3)

当内核分派的 Task 涉及 HIGH_RISK capability（如 `file.delete`、`system.config.write`、`message.send_external`），
内核必须暂停分派，经来源通道向用户发起审批请求，并等待用户确认或拒绝；默认超时视为拒绝。只有收到明确"批准"时才真正分派。

**Why this priority**: 自动化系统若无人工刹车，任何幻觉/注入都可能酿成事故。在 P2 幂等保障之后，P3 是"敢不敢对外放的最后一道门"。

**Independent Test**: 实现 P3 后，可通过 stub Worker 声明 `file.delete` 并打上 HIGH_RISK 标签，模拟用户 (a) 批准、(b) 拒绝、(c) 超时默认拒绝 三种分支，验证 Task 状态与审计事件均符合预期。

**Acceptance Scenarios**:

1. **Given** Worker `danger-stub` 声明 `file.delete` 且标注 HIGH_RISK，
   **When** Alpha 用户提交一条映射到 `file.delete` 的指令，
   **Then** 内核向 CLI 回写"审批请求"消息，Task 状态为 `pending_approval`，不分派给 Worker。
2. **Given** `pending_approval` 的 Task 存在，
   **When** Alpha 用户回写"approve <traceId>"，
   **Then** Task 状态变为 `dispatched` 并派发给 Worker；审计日志出现 `approval_granted` 事件。
3. **Given** `pending_approval` 的 Task 存在，
   **When** Alpha 用户在默认审批窗口期内未响应，
   **Then** Task 状态变为 `denied(reason=approval_timeout)`，不被派发；审计日志出现 `denied_by_timeout` 事件。
4. **Given** `pending_approval` 的 Task 存在，
   **When** Alpha 用户回写"deny <traceId>"，
   **Then** Task 状态变为 `denied(reason=user_rejected)`，不被派发。

---

### User Story 4 — 一键取消 (Priority: P4)

Alpha 用户对一个正在运行中的 traceId 投递"取消"指令，编排内核必须在约定时间内将该 traceId 下所有 running / dispatched / pending_approval 的 Task 置为 cancelled，并向相应 Worker 发送软中止信号。

**Why this priority**: "能否刹车"是自动化系统信任的下限，但只有在 P1-P3 具备之后讨论刹车才有意义，故列 P4。

**Independent Test**: 通过一个"故意慢"的 stub Worker（sleep 30 秒）触发一条多叶 Task 树；在其 running 期间提交 cancel 指令；
验证 (a) 所有相关 Task 在 ≤ 5 秒内转为 cancelled，(b) 对应 Worker 收到软中止信号，(c) 审计日志出现 `cancel_requested`、`soft_abort_sent`、`cancelled` 等事件，(d) 无相关 Task 继续生产副作用。

**Acceptance Scenarios**:

1. **Given** traceId=T4 下有 3 个 Task 处于 `running`，
   **When** Alpha 用户投递 `{cancel: T4}`，
   **Then** 内核 ≤ 5 秒内将 3 个 Task 状态均置为 `cancelled`，并向对应 Worker 发送软中止信号。
2. **Given** traceId=T5 下有 Task 处于 `pending_approval`，
   **When** 收到取消指令，
   **Then** Task 状态变为 `cancelled(reason=user_cancel_before_approval)`，不再进入审批流。
3. **Given** 不存在的 traceId=T6，
   **When** 收到取消指令，
   **Then** 内核返回 `not_found`，审计日志记录无效取消请求。

---

### User Story 5 — 崩溃隔离 (Priority: P5)

任一 Worker 崩溃（进程退出 / 抛未捕获异常 / 超过资源上限）时，编排内核必须将其上承载的 Task 置为 `failed(reason=worker_crashed|sandbox_limit)`，且不得影响其他 Worker、其他 Task 或内核主循环。

**Why this priority**: 系统韧性的守门员。在 P1-P4 功能全有之后，P5 保证"真实世界总会抖一下时"系统不整体垮掉。

**Independent Test**: 实现 P5 后，可同时派发两条独立 trace，其中一条让 stub Worker `crash()`，验证 (a) 崩溃 Worker 承载的 Task 正确置 failed，(b) 另一条 trace 完成率 100%，(c) 内核主循环未中断，(d) 审计日志完整记录崩溃原因。

**Acceptance Scenarios**:

1. **Given** traceId=T7 与 T8 并行运行，T7 的 Worker 抛出未捕获异常，
   **When** 异常发生，
   **Then** T7 对应 Task 置 `failed(reason=worker_crashed)`；T8 继续正常运行至 `succeeded`；内核未重启。
2. **Given** Worker 被配置为内存上限 128 MB，实际使用达 256 MB，
   **When** 沙箱检测到超限，
   **Then** Worker 被强制终止，对应 Task 置 `failed(reason=sandbox_limit)`，审计记录资源峰值。
3. **Given** Worker 不响应软中止信号超过 3 秒，
   **When** 超时触发硬终止流程，
   **Then** 内核强制 kill 该 Worker，Task 置 `failed(reason=hard_terminated)`。

---

### Edge Cases

- **重复 cancel**：对同一 traceId 连续两次 cancel —— 首次生效，后续返回 `already_cancelled`，不重复发送软中止信号。
- **approval 与 cancel 竞态**：用户已提交 `approve T9`，但在内核实际分派前又提交 `cancel T9` —— 以**最后到达**指令为准；若 cancel 后到，Task 置 cancelled 且不派发；审计记录竞态事件。
- **capability 不可用 vs HIGH_RISK 双重失败**：映射到某 capability 且该 capability 被标注 HIGH_RISK，但无 Worker 声明该能力 —— 应直接 `failed(reason=no_capable_worker)`，不走审批流（审批非正常动作的资源是浪费）。
- **事件 ID 冲突于不同用户**：两用户提交的 eventId 恰好相同 —— 系统 MUST 按 `(userId, eventId)` 作为幂等键，不互相遮蔽。
- **审计日志磁盘写满**：新事件无法落盘时，内核 MUST 拒绝处理新入口事件并发出健康告警，**不允许**为节省空间静默丢弃日志。
- **Worker 注册信息与实际能力不符**：Worker 声明 capability 但实际未实现 —— 首次调用时检测到后立即置 Worker 为 `unhealthy`，该 capability 从分派候选中剔除直至下一次健康心跳。
- **超大 payload**：入口事件 `text` 字段超过 16 KB（默认阈值，见 FR-031）—— 内核 MUST 立即拒绝并回 `rejected(reason=payload_too_large, limit=16KB, actual=<bytes>)`，MUST NOT 截断后继续处理，MUST NOT 创建 Task 树，并按 FR-019 结构落审计。
- **HIGH_RISK 审批洪水尝试**：同一用户在既有 HIGH_RISK Task 仍 `pending_approval` 时再次投递任何 HIGH_RISK 事件 —— 内核 MUST 按 FR-025 第二条拒绝新事件并回 `rejected(reason=rate_limited, dimension=user_highrisk_concurrent)`，不得排队、不得触发第二次审批消息，避免审批通道被刷屏。
- **内核崩溃 / 异常重启**：内核进程被 kill、OOM 或宿主机重启后再次启动 —— 新内核 MUST 在启动序列中扫描最近一次审计日志，将所有未见终态的 in-flight Task（`pending | dispatched | running | pending_approval`）补写为 `failed(reason=kernel_restart)` 事件，并为每个受影响 traceId 生成一份"中断汇总"推送给对应 userId（见 FR-028 / FR-029）。用户以**新 eventId** 重投原指令；复用旧 eventId MUST 被幂等层拒绝。

## Requirements *(mandatory)*

### Functional Requirements

**入口与事件协议**

- **FR-001**: 系统 MUST 定义统一的入口事件 schema，至少包含 `eventId`、`userId`、`text`、`sourceChannel`、`receivedAt` 五字段；非法 payload MUST 被立即拒绝并回写 `rejected(reason=malformed_event)`。
- **FR-002**: 系统 MUST 以 `(userId, eventId)` 作为幂等键；重复投递不得产生新 Task 树。
- **FR-003**: 系统 MUST 支持至少一种本地入口（CLI 或本地 HTTP），该入口仅用于 MVP；真实飞书入口由后续功能引入。

**Task 生命周期**

- **FR-004**: 系统 MUST 为每个事件创建一棵 Task 树，根节点代表"用户意图"，叶节点代表"发给某 Worker 的动作请求"，每个 Task 持有稳定的 `taskId` 与 `parentTaskId`。
- **FR-005**: Task 状态机 MUST 为 `pending → dispatched → running → (succeeded | failed | cancelled | denied)`，状态转换 MUST 单向（除非经特定规则如重试，且重试能力不在本 MVP 范围）。
- **FR-006**: 系统 MUST 为每一次状态变更、派发、结果返回、审批、取消、崩溃写入一条结构化审计事件。

**Worker 注册与分派**

- **FR-007**: 系统 MUST 维护 Worker 注册表，记录每个 Worker 的 `workerId`、所支持的 `capabilities[]`、每项能力的 `risk_level`（`NORMAL` | `HIGH_RISK`）、`resource_limits`（内存、CPU、墙钟上限）。
- **FR-008**: 系统 MUST 仅将标注某 capability 的 Task 分派给声明了该 capability 的健康 Worker；无匹配者 MUST 置 Task 为 `failed(reason=no_capable_worker)`。
- **FR-009**: 系统 MUST 在 Worker 连续 N 次（默认 3 次）心跳缺失或健康探测失败时，将其标记为 `unhealthy` 并移出分派候选集。

**HIGH_RISK 审批门（对应 Constitution III）**

- **FR-010**: 所有 `risk_level=HIGH_RISK` 的 Task 在分派前 MUST 进入 `pending_approval` 状态并通过来源通道向用户发起审批请求。
- **FR-011**: 审批窗口期默认为 **10 分钟**（自 `pending_approval` 进入该状态起计时）；超时 MUST 视为拒绝并落盘 `denied_by_timeout`。具体阈值可在部署配置中覆写，但 MVP 默认值保持 10 分钟。
- **FR-012**: 用户回写 `approve <traceId>` / `deny <traceId>` MUST 以来源通道的消息形式接受；内核 MUST 按 `(userId, traceId)` 校验回写者身份，拒绝他人越权审批。

**可取消性（对应 Constitution III）**

- **FR-013**: 系统 MUST 在收到 `cancel <traceId>` 指令后，将该 traceId 下所有 `running | dispatched | pending_approval` 的 Task 置为 `cancelled`；响应的生效时间上限 ≤ 5 秒。
- **FR-014**: 系统 MUST 向被取消 Task 对应的 Worker 发送软中止信号；Worker 在 3 秒内未响应时 MUST 升级为硬终止。
- **FR-015**: 对不存在的 traceId，取消指令 MUST 返回 `not_found`，并在审计日志中记录。

**沙箱与隔离（对应 Constitution V）**

- **FR-016**: 每个 Worker MUST 运行于独立进程（容器为可选增强，不作为 MVP 硬要求）；其崩溃不得影响主循环或其他 Worker。
- **FR-017**: Worker MUST 受资源上限约束（内存、CPU、墙钟时间）；超限由内核强制终止并将对应 Task 置为 `failed(reason=sandbox_limit)`。
- **FR-018**: 每次任务分派 MUST 受三维预算约束（`wall_clock_ms`、`max_tool_calls`、`max_tokens`）。预算值来源优先级：
  1. Worker 在注册时为该 capability 声明的预算；
  2. 未声明则 fallback 到**保守默认**：`wall_clock_ms=60_000` / `max_tool_calls=10` / `max_tokens=20_000`；
  3. 任何声明或 fallback 值 MUST NOT 超过**系统硬顶**：`wall_clock_ms=1_800_000` / `max_tool_calls=200` / `max_tokens=500_000`，超硬顶的声明 MUST 被注册阶段拒绝。

  运行期任一维度超限由内核终止该 Task，置 `failed(reason=budget_exceeded, dim=<wall|tool|token>)`，并按 FR-017 的沙箱超限路径处理。

**审计与可观测（对应 Constitution IV）**

- **FR-019**: 每条审计事件 MUST 至少包含 `traceId`、`taskId`、`parentTaskId`、`actor`、`capability`、`input_hash`、`output_hash`、`timestamp`、`outcome` 字段，且 MUST 可按 traceId 完整回放。
- **FR-020**: 敏感字段（用户身份标识符以外的个人信息、凭据、长文本 payload）在审计日志中 MUST 被 hash 或 mask，禁止明文落盘。
- **FR-021**: 审计日志 MUST 持久化到本地文件系统，默认保留期 ≥ 30 天；磁盘不可写时 MUST 拒绝处理新事件而非静默丢弃。

**幂等（对应 Constitution VI）**

- **FR-022**: 重复投递同 `(userId, eventId)` 的事件 MUST NOT 产生新的 Task 树；MUST 返回首次处理的 traceId 与当前状态或 final result。
- **FR-023**: 系统 MUST 在审计日志中对重复请求打上 `idempotent_replay=true` 标记。

**契约先行（对应 Constitution VII）**

- **FR-024**: 入口事件、Task 树消息、Worker 注册消息、审批消息、取消消息的 schema MUST 在 plan 阶段以机器可校验格式先行定义；实现代码只允许消费已定义的 schema。

**速率限制与并发控制（对应 Constitution II / III 的滥用防御）**

- **FR-025**: 每个 capability MUST 在注册信息中明确其 `risk_level`（`NORMAL` | `HIGH_RISK`），并据此适用不同的并发规则：
  - `NORMAL`：同一用户并发活跃 trace（状态为 `pending | dispatched | running | pending_approval` 之和）MUST ≤ 10；
  - `HIGH_RISK`：同一用户"未决 / 运行中 HIGH_RISK Task"数量 MUST ≤ 1（新 HIGH_RISK 事件 MUST 在既有 HIGH_RISK Task 结束前被拒绝）。
- **FR-026**: 入口层 MUST 执行双层速率限制：
  - 全局入口吞吐峰值 MUST ≤ 50 events/sec（含所有用户、所有来源通道汇总）；
  - 每用户入口事件速率 MUST ≤ 120 events/min。
  以上阈值在部署配置中可覆写，但覆写值不得高于系统硬顶（全局 ≤ 500 events/sec、每用户 ≤ 1200 events/min）。
- **FR-027**: 任一种类超限 MUST 返回 `rejected(reason=rate_limited, dimension=<global_rps|user_rpm|user_concurrent|user_highrisk_concurrent>)`，MUST 不入 Task 队列、MUST 不触发 Task 创建，并 MUST 在审计日志中按 FR-019 的结构完整落盘（含 traceId-less `rejection_event`）。

**崩溃恢复与结果回推（对应 Constitution III / IV / VI）**

- **FR-028**: 内核状态以"内存为工作态、审计日志为事实来源"的两层模型组织，MUST NOT 假设 Task 状态跨进程持久化。内核启动时 MUST 执行一次"审计扫描"：
  1. 读取最近一次运行的审计日志尾段；
  2. 对所有已出现 `pending | dispatched | running | pending_approval` 但**未出现终态**的 Task，补写一条 `failed(reason=kernel_restart)` 审计事件；
  3. 扫描完成前 MUST 拒绝一切入口事件，回 `rejected(reason=kernel_warming_up)`。
- **FR-029**: 对每个 trace 的**终态**（`succeeded | failed | cancelled | denied | denied_by_timeout` 中的任一），内核 MUST 主动经**原来源通道**向发号者推送一条"结果汇总消息"，至少包含：
  - `traceId`、原 `eventId`、用户自然语言指令摘要；
  - 每个叶 Task 的 `taskId`、`capability`、最终 `outcome`、失败原因（如有）；
  - 本 trace 的整体结论（全部成功 / 部分失败 / 整体失败 / 被取消 / 被拒绝）；
  - 对 `failed(reason=kernel_restart)` 的 trace，MUST 在结论中显式说明"系统中断，原指令未完成，请以新指令重投"。

  该推送 MUST 受 FR-020 的敏感字段脱敏规则约束；推送失败（通道不可达）MUST 至少重试 3 次（指数退避），全失败后 MUST 在审计日志中落盘 `notification_delivery_failed`，但 MUST NOT 重写 Task 终态。
- **FR-030**: 用户 MUST NOT 被要求通过轮询拿结果；对任一 trace，"事件入队 → 终态回推"的可观测闭环由内核负责维护。CLI / 本地 HTTP 入口在 MVP 下 MUST 至少提供一种与推送一致的结果展示（例如进程前台打印 / webhook 回调），实现形式由 plan 决定。

**Payload 大小与入口健壮性**

- **FR-031**: 入口事件 `text` 字段默认上限 MUST 为 **16 KB**（UTF-8 编码字节数）；超限 MUST 在校验阶段立即拒绝并回 `rejected(reason=payload_too_large, limit=16KB, actual=<bytes>)`，MUST NOT 触发任何 Task 创建或 LLM 调用。阈值 MUST 在部署配置中可覆写，但覆写值 MUST ≤ 系统硬顶 **1 MB**，超硬顶的配置 MUST 在内核启动时被拒绝。该校验 MUST 先于 FR-001 的其他字段校验执行，以防恶意超大 payload 耗尽校验资源。

### Key Entities

- **Entry Event (入口事件)**：来自外部通道的单条指令请求，携带幂等键、用户身份、原始文本、来源通道、时间戳。
- **Task**：任务树中的一个节点，具有稳定 ID、状态、所属 capability、父节点、生命周期事件流；根 Task 代表用户意图，叶 Task 代表具体动作请求。
- **Worker Agent (Worker)**：注册到内核的执行单元，声明自己支持的 capability 集合与资源限制；MVP 阶段以 stub 形式存在。
- **Capability**：Worker 可以执行的原子能力的命名（如 `echo.say`、`file.delete`），携带 `risk_level` 标签。
- **Approval Request (审批请求)**：针对 HIGH_RISK Task 在分派前产生的待决项，绑定 traceId + userId，具有默认超时窗口。
- **Audit Event (审计事件)**：任一状态变更、派发、结果、审批、取消、崩溃的结构化记录，作为可回放的系统事实来源。
- **Trace**：一次用户意图的完整执行链，由 traceId 聚合事件；一条入口事件对应一个 trace。
- **Result Summary (结果汇总消息)**：对一个 trace 终态的用户可读汇总，绑定 traceId + 原 eventId + userId，经来源通道主动回推；生成时机为 trace 终态事件落审计日志后，内容服从 FR-020 的脱敏规则。
- **Result Summary Notification (结果汇总消息)**：trace 到达任一终态后，由内核主动向来源通道推送的结构化汇总，聚合本 trace 下所有叶 Task 的 outcome，是用户获取结果的首选路径（用户不依赖轮询）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 新 Alpha 用户能在 ≤ 3 分钟内完成一次端到端演练：提交一条指令 → 看到 Task 树 → 查看审计日志 → 复查幂等效果。
- **SC-002**: 基本闭环的端到端响应时间（从入口收到事件到来源通道收到最终结果）在 stub Worker 响应耗时 ≤ 500 ms 的前提下 95 分位 ≤ 3 秒。
- **SC-003**: 幂等实验（同 eventId 短时间内重复投递 5 次）下，Task 树创建次数 = 1、Worker 调用次数 = 1、副作用计数 = 0，100% 复现。
- **SC-004**: 一键取消的 traceId 内所有相关 Task 在 ≤ 5 秒内变为 cancelled；实测平均生效时间 ≤ 2 秒。
- **SC-005**: 在"3 条并发 trace 中人为 crash 1 个 Worker"的场景，其余 2 条 trace 的完成率 100%，内核主循环零重启。
- **SC-006**: 任意 traceId 可在 ≤ 5 秒内从审计日志完整回放；sample 抽查 20 条 trace，100% 字段齐全且无明文敏感数据。
- **SC-007**: HIGH_RISK Task 在未明确批准的前提下被分派的次数 = 0（以审计日志为准，覆盖 approve / deny / timeout 三分支各 ≥ 5 次实验）。
- **SC-008**: 对"审计日志磁盘不可写"的故障注入，内核 MUST 在 10 秒内转为"拒绝新入口事件"状态，且不丢任何已收事件的既有审计链。
- **SC-009**: 对"内核重启"的故障注入（在 3 条 in-flight trace 运行中 kill 主进程），重启后 ≤ 10 秒内：全部 3 条 trace 的审计日志补齐 `failed(reason=kernel_restart)` 终态，且对应结果汇总消息全部经来源通道投递成功（含 ≤ 3 次重试路径），发号者无需手动查询即可获知中断事实。
- **SC-010**: 所有 trace 终态的结果汇总消息首次投递成功率 ≥ 99%，重试后累计投递成功率 = 100%；样本不少于 50 条 trace。
- **SC-011**: 注入 100 条超大 payload（32 KB ~ 2 MB 各量级各 20 条）的入口事件，全部 MUST 在入口接收后 ≤ 50 ms 内被拒绝并落审计，Task 队列长度增量 = 0，内核 CPU / 内存占用无异常尖峰。
- **SC-009**: 对"内核进程中途 kill"的故障注入，重启后 ≤ 10 秒内完成审计扫描；所有 in-flight trace MUST 在重启后 ≤ 30 秒内收到 `failed(reason=kernel_restart)` 的结果汇总推送，推送送达率（含 3 次重试后）≥ 99%；失败的推送 100% 在审计日志中留有 `notification_delivery_failed` 记录。
- **SC-010**: 对任一 trace 的终态，来源通道收到的结果汇总 MUST 与审计日志按 traceId 重放出的终态 100% 一致；sample 抽查 50 条 trace 无漂移。

## Assumptions

- 本阶段部署场景为**单机单进程内核 + 多 Worker 子进程**，多节点分布式延后。
- MVP 的入口通道仅要求支持本地 CLI 或本地 HTTP 之一；飞书、Webhook、桌面 UI 等入口作为后续功能延后。
- 用户身份以外部通道传入的字符串形式（如 OS 用户名或通道提供的 openId）为准，MVP 内核不做独立身份认证。
- 审计日志写入本地文件系统，保留期 30 天内；异地归档、合规审计等延后。
- 审批消息与取消指令可通过与来源通道相同的入口回写；MVP 不要求独立审批 UI。
- 所有具体技术选型（语言、消息中间件、沙箱技术、LLM 调用抽象等）由 `/speckit-plan` 阶段决定；spec 层面保持技术无关。
- HIGH_RISK capability 的具体命名与分类清单将在 plan 或后续 feature spec 中维护；MVP 只需支持 "声明 → 识别 → 审批门" 机制本身。
