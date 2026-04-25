# Phase 10 批次拆解（Draft）

> 目标：把 Phase 10 从“总体方案”继续拆成可 review、可实现、可验证的批次。
> 
> 原则：先契约、后实现；先主 agent、后子 agent；先闭环、后扩展。

---

## Batch A — 契约冻结

**目的**：把主 / 子 agent 之间最基础的消息边界定下来，避免后面实现时反复改字段。

### A1. 统一请求模型
- `text`
- `userId`
- `eventId`
- `sourceChannel`
- `traceId`（如需要由主 agent 生成，则说明来源）

### A2. 统一响应模型
- `traceId`
- `eventId`
- `traceOutcome`
- `message`
- `data` / `result`（结构化结果区）
- `audit_event_types`

### A3. capability 声明模型
- `agentId`
- `capability`
- `version`
- `riskLevel`
- `resourceLimits`
- `health`

### A4. cancel / status / health 模型
- `cancelRequest`
- `cancelResponse`
- `statusRequest`
- `statusResponse`
- `healthReport`

### A5. 审计事件最小集合
- `entry_received`
- `route_decided`
- `task_dispatched`
- `task_started`
- `task_succeeded`
- `task_failed`
- `task_cancelled`
- `task_timed_out`
- `result_returned`

**验收**
- 字段稳定
- 可序列化
- 可反序列化
- 可审计
- 可回放

---

## Batch B — 契约测试

**目的**：先让所有契约测试失败，再补实现。

### B1. 请求模型契约测试
- 正例
- 缺字段
- 非法字段值
- sourceChannel 非法枚举

### B2. 响应模型契约测试
- 正常成功
- 失败结果
- 取消结果
- 超时结果

### B3. capability 声明测试
- 单 capability 限制
- worker 注册 / 发现字段
- health 字段一致性

### B4. 通信协议测试
- request / response 往返
- cancel 请求幂等
- status / health 可用

### B5. 审计测试
- 审计字段完整
- 审计顺序一致
- 审计可回放

**验收**
- 测试先 RED
- 契约变化会明确暴露
- 不允许测试只测实现细节

---

## Batch C — 主 agent 核心闭环

**目的**：先让主 agent 能调度一个独立 worker，完成最小闭环。

### C1. 主 agent 路由器
- capability-based 匹配
- 选择可用 worker
- 无匹配失败

### C2. registry / health
- worker 注册
- worker 发现
- worker 健康状态维护

### C3. 调度器
- 下发任务
- 等待回传
- 失败处理
- 超时处理

### C4. 审计接入
- 路由决策
- 分发
- 回传
- 失败
- 取消

### C5. 取消处理
- 主 agent 接收取消
- 转发给执行中的 worker
- 记录终态

**验收**
- 主 agent 可以完整调度一个 worker
- 有明确失败路径
- 审计链完整

---

## Batch D — 子 agent worker 闭环

**目的**：做一个最小独立 worker，验证它能被主 agent 调度。

### D1. worker 入口
- 独立进程启动
- 注册到主 agent

### D2. capability 声明
- 单 capability
- 单职责
- 版本可见

### D3. 任务执行
- 收到任务
- 执行任务
- 返回结构化结果

### D4. 失败 / 超时 / 取消
- 失败可结构化回传
- 超时可上报
- 取消可终止

**验收**
- worker 能独立跑
- worker 能被调度
- worker 不依赖入口层

---

## Batch E — 入口统一接入

**目的**：把 CLI / HTTP / Feishu 都接入统一主流程。

### E1. CLI 接主 agent
- CLI 不直接编排
- 只负责标准化后调用主 agent

### E2. HTTP 接主 agent
- HTTP 不直接编排
- 只做请求映射

### E3. Feishu 接主 agent
- Feishu 只做消息适配
- 不碰执行逻辑

### E4. 统一回传
- 入口收到主 agent 返回结果后再回给用户
- 审计保留来源渠道

**验收**
- 三入口进入同一主流程
- 没有入口分裂逻辑

---

## Batch F — 全链路联调与回放

**目的**：验证完整系统行为与可回放性。

### F1. 端到端闭环
- 入口 → 主 agent → worker → 回传

### F2. 取消 / 超时 / 失败联动
- 各种异常都能闭环

### F3. 审计回放
- 仅凭审计可重建 trace

### F4. 回归稳定性
- 重复执行结果一致
- 失败路径可解释

**验收**
- 真正可演示
- 可 review
- 可回放

---

## 推荐执行顺序

1. Batch A 契约冻结
2. Batch B 契约测试
3. Batch C 主 agent 闭环
4. Batch D 子 agent worker
5. Batch E 入口统一接入
6. Batch F 联调与回放

---

## 说明

- 第一版只允许单 capability 子 agent。
- 第一版子 agent 必须是独立进程 / worker。
- 路由方式第一版固定使用 capability-based 规则匹配。
- 第二阶段最优先目标是审计全链路与可回放。
- 该拆解用于后续正式 TDD 落地前 review。
