# Phase 10 Schema / Model 草图

> 目标：把 Phase 10 的契约字段进一步收敛成可直接落地到 JSON Schema / Pydantic v2 的草图。
> 
> 说明：这是设计草图，不是最终实现代码。后续如果进入实现阶段，可直接据此拆成契约测试与 pydantic 模型文件。

---

## 1. 设计原则

- 字段尽量少，但必须足够支撑路由、执行、审计、取消、失败、回放。
- 所有跨边界消息都必须是结构化对象。
- 第一版只支持单 capability 子 agent。
- 第一版子 agent 必须是独立进程 / worker。
- 审计字段优先于“为了方便”而加入的自由扩展字段。

---

## 2. `AgentRequest` 草图

### 用途
主 agent 的统一输入。

### JSON 结构草图

```json
{
  "text": "echo hello",
  "userId": "alice",
  "eventId": "01KQ...",
  "sourceChannel": "cli",
  "traceId": null,
  "timestamp": "2026-04-25T12:00:00Z",
  "metadata": {}
}
```

### 约束草图
- `text` required, string, minLength 1
- `userId` required, string, minLength 1
- `eventId` optional, string when present
- `sourceChannel` required, enum: `cli` | `http` | `feishu_stub`
- `traceId` optional, string when present
- `timestamp` optional, ISO 8601 string when present
- `metadata` optional, object, default `{}`

### Pydantic 草图
- `ConfigDict(extra="forbid")`
- `text: str`
- `userId: str`
- `eventId: str | None = None`
- `sourceChannel: Literal["cli", "http", "feishu_stub"]`
- `traceId: str | None = None`
- `timestamp: datetime | None = None`
- `metadata: dict[str, Any] = Field(default_factory=dict)`

---

## 3. `AgentCapability` 草图

### 用途
子 agent 对外声明能力。

### JSON 结构草图

```json
{
  "agentId": "worker-echo-01",
  "capability": "echo.say",
  "version": "1.0.0",
  "riskLevel": "NORMAL",
  "healthy": true,
  "resourceLimits": {
    "memoryMb": 128,
    "cpuPct": 10,
    "wallClockMs": 60000
  },
  "description": "Echo worker",
  "lastHeartbeatAt": "2026-04-25T12:00:00Z"
}
```

### 约束草图
- `agentId` required
- `capability` required
- `version` required
- `riskLevel` required enum: `NORMAL` | `HIGH_RISK`
- `healthy` required boolean
- `resourceLimits` required object
- `description` optional
- `lastHeartbeatAt` optional datetime

### Pydantic 草图
- `ConfigDict(extra="forbid")`
- `agentId: str`
- `capability: str`
- `version: str`
- `riskLevel: Literal["NORMAL", "HIGH_RISK"]`
- `healthy: bool`
- `resourceLimits: ResourceLimits`
- `description: str | None = None`
- `lastHeartbeatAt: datetime | None = None`

---

## 4. `AgentTaskRequest` 草图

### 用途
主 agent 发给子 agent 的执行任务。

### JSON 结构草图

```json
{
  "traceId": "01KQ...",
  "eventId": "01KQ...",
  "taskId": "01KQ...",
  "parentTaskId": null,
  "capability": "echo.say",
  "payload": {
    "text": "hello"
  },
  "riskLevel": "NORMAL",
  "sourceChannel": "cli",
  "userId": "alice",
  "deadlineAt": "2026-04-25T12:00:05Z",
  "attempt": 1,
  "metadata": {}
}
```

### 约束草图
- `traceId` required
- `eventId` required
- `taskId` required
- `parentTaskId` optional
- `capability` required
- `payload` required object
- `riskLevel` required enum
- `sourceChannel` required enum
- `userId` required
- `deadlineAt` optional datetime
- `attempt` required integer, min 1
- `metadata` optional object

### Pydantic 草图
- `ConfigDict(extra="forbid")`
- 任务级别字段使用严格类型
- `payload` 不做业务内容解析，只要求可序列化

---

## 5. `AgentTaskResponse` 草图

### 用途
子 agent 回传主 agent 的结果。

### JSON 结构草图

```json
{
  "traceId": "01KQ...",
  "eventId": "01KQ...",
  "taskId": "01KQ...",
  "agentId": "worker-echo-01",
  "status": "succeeded",
  "result": {
    "text": "hello"
  },
  "errorCode": null,
  "errorMessage": null,
  "failureReason": null,
  "failureDim": null,
  "startedAt": "2026-04-25T12:00:00Z",
  "finishedAt": "2026-04-25T12:00:00Z",
  "outputHash": "sha256:...",
  "metadata": {}
}
```

### 约束草图
- 成功时 `result` required
- 失败时 `errorCode` or `failureReason` 至少一个非空
- `status` enum: `succeeded` | `failed` | `cancelled` | `timed_out`
- `outputHash` optional

### Pydantic 草图
- `ConfigDict(extra="forbid")`
- `status: Literal["succeeded", "failed", "cancelled", "timed_out"]`
- `result: dict[str, Any] | None = None`
- `errorCode: str | None = None`
- `errorMessage: str | None = None`
- `failureReason: str | None = None`
- `failureDim: str | None = None`

---

## 6. `AgentCancelRequest` 草图

### 用途
主 agent / 入口发起取消。

### JSON 结构草图

```json
{
  "traceId": "01KQ...",
  "taskId": null,
  "userId": "alice",
  "reason": "user_requested",
  "timestamp": "2026-04-25T12:00:02Z",
  "sourceChannel": "cli"
}
```

### 约束草图
- `traceId` required
- `taskId` optional
- `userId` required
- `reason` optional
- `timestamp` required
- `sourceChannel` required

### Pydantic 草图
- `ConfigDict(extra="forbid")`
- `taskId: str | None = None`
- `reason: str | None = None`

---

## 7. `AgentCancelResponse` 草图

### JSON 结构草图

```json
{
  "traceId": "01KQ...",
  "taskId": null,
  "status": "accepted",
  "message": null,
  "timestamp": "2026-04-25T12:00:03Z"
}
```

### 约束草图
- `status` enum: `accepted` | `not_found` | `already_terminal` | `already_cancelled`
- 必须结构化响应

---

## 8. `AgentStatusRequest` / `AgentStatusResponse` 草图

### Request

```json
{
  "traceId": "01KQ...",
  "agentId": null,
  "userId": null,
  "sourceChannel": null
}
```

### Response

```json
{
  "traceId": "01KQ...",
  "agentId": null,
  "status": "running",
  "message": "trace in progress",
  "lastKnownState": "running",
  "timestamp": "2026-04-25T12:00:04Z"
}
```

### 约束草图
- `status` 需可区分 trace 状态与 agent 状态
- 不建议混成一个不透明字符串

---

## 9. `AgentHealthReport` 草图

### JSON 结构草图

```json
{
  "agentId": "worker-echo-01",
  "healthy": true,
  "capability": "echo.say",
  "heartbeatAt": "2026-04-25T12:00:05Z",
  "resourceUsage": {
    "memoryMb": 23.4,
    "cpuPct": 1.2,
    "runtimeMs": 15000
  },
  "details": null
}
```

### 约束草图
- `healthy` required boolean
- `capability` required
- `heartbeatAt` required
- `resourceUsage` optional
- unhealthy 必须能影响路由

---

## 10. 审计事件草图

### `AgentAuditEvent`

建议最小字段：
- `eventType`
- `traceId`
- `taskId`
- `agentId`
- `userId`
- `sourceChannel`
- `timestamp`
- `extra`

### 事件类型草图
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

---

## 11. 设计上建议保留的最小集

第二阶段第一版建议只保留这些字段/模型：

- `AgentRequest`
- `AgentCapability`
- `AgentTaskRequest`
- `AgentTaskResponse`
- `AgentCancelRequest`
- `AgentCancelResponse`
- `AgentStatusRequest`
- `AgentStatusResponse`
- `AgentHealthReport`
- `AgentAuditEvent`

其余扩展字段先不要引入，避免第二阶段一开始就复杂化。

---

## 12. 下一步实现映射建议

如果后续进入实现，建议按照这个顺序拆：

1. 先写契约测试
2. 再写 pydantic 模型
3. 再写主 agent 路由器
4. 再写子 agent worker
5. 最后做入口统一接入

---

## 13. 备注

- 这是 Phase 10 的 schema/model 草图，不是最终代码。
- 如果 review 通过，可以直接把这些草图转成 JSON Schema + pydantic v2 模型。
- 若后续需要，我可以继续把这份草图拆成“契约测试目录结构建议”。
