# Phase 10 契约字段草案

> 目标：在进入实现前，把主 agent / 子 agent / 入口统一流程所需的消息字段定死。
> 
> 原则：字段尽量少，但要足够支撑路由、执行、审计、取消、失败、回放。

---

## 1. 统一内部请求模型

这是 CLI / HTTP / Feishu 最终都要转换成的统一请求。

### `AgentRequest`

**用途**：主 agent 的标准输入。

**字段**
- `text: string` — 用户原始指令文本
- `userId: string` — 发起人身份
- `eventId: string | null` — 入口提供的幂等键；空则由主 agent 生成
- `sourceChannel: string` — 来源渠道，第一版取值：`cli` / `http` / `feishu_stub`
- `traceId: string | null` — 可选；若入口未提供则由主 agent 生成
- `timestamp: string | null` — 可选；入口侧接收时间，ISO 8601
- `metadata: object | null` — 可选；入口附加信息，默认空对象

**约束**
- `text` 必填
- `userId` 必填
- `sourceChannel` 必填
- `eventId` 可空但一旦存在必须稳定
- `metadata` 只允许可审计的非敏感信息

---

## 2. 主 agent 路由决策模型

### `RouteDecision`

**用途**：主 agent 选择哪个子 agent 执行任务。

**字段**
- `traceId: string`
- `eventId: string`
- `selectedAgentId: string`
- `selectedCapability: string`
- `decisionReason: string`
- `decisionType: string` — 例如 `capability_match` / `no_match` / `unhealthy`
- `fallbackUsed: boolean`
- `timestamp: string`

**约束**
- 路由决策必须可审计
- 无匹配时必须给出结构化失败结果
- 第一版只支持 capability-based 规则匹配

---

## 3. 子 agent capability 声明模型

### `AgentCapability`

**用途**：子 agent 向主 agent 声明自己能做什么。

**字段**
- `agentId: string`
- `capability: string`
- `version: string`
- `riskLevel: string` — 例如 `NORMAL` / `HIGH_RISK`
- `healthy: boolean`
- `resourceLimits: object`
- `description: string | null`
- `lastHeartbeatAt: string | null`

**resourceLimits 字段**
- `memoryMb: integer`
- `cpuPct: integer`
- `wallClockMs: integer`

**约束**
- 第一版一个 worker 只声明一个 capability
- capability 必须能被路由器直接匹配
- health 变化必须可见

---

## 4. 主 / 子 agent 任务请求模型

### `AgentTaskRequest`

**用途**：主 agent 发给子 agent 的执行请求。

**字段**
- `traceId: string`
- `eventId: string`
- `taskId: string`
- `parentTaskId: string | null`
- `capability: string`
- `payload: object`
- `riskLevel: string`
- `sourceChannel: string`
- `userId: string`
- `deadlineAt: string | null`
- `attempt: integer`
- `metadata: object | null`

**约束**
- `taskId` 必填
- `capability` 必填
- `payload` 必须可序列化
- `attempt` 从 1 开始
- `deadlineAt` 若存在必须可被 worker 理解

---

## 5. 子 agent 任务响应模型

### `AgentTaskResponse`

**用途**：子 agent 回传主 agent 的结果。

**字段**
- `traceId: string`
- `eventId: string`
- `taskId: string`
- `agentId: string`
- `status: string` — `succeeded` / `failed` / `cancelled` / `timed_out`
- `result: object | null`
- `errorCode: string | null`
- `errorMessage: string | null`
- `failureReason: string | null`
- `failureDim: string | null`
- `startedAt: string | null`
- `finishedAt: string | null`
- `outputHash: string | null`
- `metadata: object | null`

**约束**
- 成功时 `result` 必须存在
- 失败时 `errorCode` / `failureReason` 至少要有一个
- 取消和超时必须结构化，不允许只返回空字符串

---

## 6. cancel 协议

### `AgentCancelRequest`

**字段**
- `traceId: string`
- `taskId: string | null`
- `userId: string`
- `reason: string | null`
- `timestamp: string`
- `sourceChannel: string`

**约束**
- `traceId` 必填
- `userId` 必填
- `taskId` 可空；空表示取消整个 trace
- 取消请求必须幂等

### `AgentCancelResponse`

**字段**
- `traceId: string`
- `taskId: string | null`
- `status: string` — `accepted` / `not_found` / `already_terminal` / `already_cancelled`
- `message: string | null`
- `timestamp: string`

**约束**
- 必须结构化回传
- 已终态任务不能被重复取消成新状态

---

## 7. status 协议

### `AgentStatusRequest`

**字段**
- `traceId: string | null`
- `agentId: string | null`
- `userId: string | null`
- `sourceChannel: string | null`

**用途**
- 查询 trace 状态
- 查询 agent 当前健康状态

### `AgentStatusResponse`

**字段**
- `traceId: string | null`
- `agentId: string | null`
- `status: string`
- `message: string | null`
- `lastKnownState: string | null`
- `timestamp: string`

---

## 8. health 协议

### `AgentHealthReport`

**字段**
- `agentId: string`
- `healthy: boolean`
- `capability: string`
- `heartbeatAt: string`
- `resourceUsage: object | null`
- `details: string | null`

**resourceUsage 字段建议**
- `memoryMb: number | null`
- `cpuPct: number | null`
- `runtimeMs: number | null`

**约束**
- heartbeats 必须能更新最后可见时间
- unhealthy 状态必须影响路由决策

---

## 9. 审计事件最小集合

### `AgentAuditEvent`

**建议事件类型**
- `entry_received`
- `route_decided`
- `task_dispatched`
- `task_started`
- `task_succeeded`
- `task_failed`
- `task_cancelled`
- `task_timed_out`
- `result_returned`
- `cancel_requested`
- `cancel_accepted`
- `cancel_rejected`
- `agent_registered`
- `agent_heartbeat`
- `agent_unhealthy`
- `agent_recovered`

**建议字段**
- `eventType: string`
- `traceId: string | null`
- `taskId: string | null`
- `agentId: string | null`
- `userId: string | null`
- `sourceChannel: string | null`
- `timestamp: string`
- `extra: object | null`

**约束**
- 所有跨边界动作必须有审计
- 审计事件必须能支撑回放
- 敏感字段继续走现有脱敏规则

---

## 10. 第一版最小协议边界

第二阶段第一版只保留以下能力：

- 统一请求模型
- capability 声明
- 路由决策
- 任务请求 / 响应
- cancel / status / health
- 审计事件

### 第一版不引入
- LLM 路由字段
- 多 capability worker
- 多级 agent 树
- 复杂策略切换字段
- 云端部署专用字段

---

## 11. 推荐落地顺序

1. 先冻结 `AgentRequest` / `AgentTaskRequest` / `AgentTaskResponse`
2. 再冻结 `AgentCapability` / `AgentHealthReport`
3. 再冻结 cancel / status 协议
4. 最后固定审计事件集合

---

## 12. 备注

- 以上字段为 Phase 10 契约草案。
- 正式实现前，建议把每个字段映射到具体 pydantic 模型与 JSON Schema。
- 若后续 review 发现字段过多，优先删减 `metadata` 的自由度，而不是削弱审计与路由主字段。
