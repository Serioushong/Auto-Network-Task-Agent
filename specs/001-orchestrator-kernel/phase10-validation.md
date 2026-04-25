# Validation: Phase 10 — 主 Agent / 子 Agent 协作体系（Draft）

**Branch**: `hjx`
**Purpose**: 定义第二阶段（主 agent / 子 agent 协作体系）的验证标准、证据要求与 review 门槛，确保后续实现按 spec-plan-harness 流程推进。

---

## 1. 验证目标

Phase 10 的验证重点不是“功能有没有做出来”，而是：

1. 主 agent 与子 agent 的职责边界是否清晰
2. 主 / 子 agent 的契约是否先行且一致
3. 主 agent 是否能按 capability 规则路由到独立进程子 agent
4. CLI / HTTP / Feishu 是否统一进入主流程
5. 审计是否能覆盖全链路并支持回放
6. 取消 / 超时 / 失败是否有明确、可重复、可审计的语义

---

## 2. 验证层级

Phase 10 验证分四层：

### 2.1 设计层验证
检查文档和任务定义是否足够完整，能支持后续实现。

**通过标准**
- 主 / 子 agent 职责无重叠
- 入口层 / 控制层 / 执行层边界清晰
- capability-based 路由规则定义明确
- 单能力、独立进程 worker 约束明确

**证据要求**
- Phase 10 design 文档最终版
- Phase 10 tasks 文档
- review checklist

---

### 2.2 契约层验证
检查主 / 子 agent 之间的数据模型和协议是否先行定义。

**通过标准**
- 请求 / 响应 schema 完整
- capability 声明模型完整
- cancel / status / health / failure 语义明确
- schema 与 pydantic 模型一致

**证据要求**
- Phase 10 contracts 草案
- Phase 10 schema/model 草图
- 契约测试清单

---

### 2.3 执行层验证
检查主 agent 是否真能把任务路由给子 agent 并拿回结果。

**通过标准**
- 主 agent 能选中正确 capability 的子 agent
- 子 agent 以独立进程 / worker 方式运行
- 子 agent 能执行单一任务并回传结构化结果
- 失败、超时、取消都能被主 agent 正确感知

**证据要求**
- 主 agent 路由测试
- 子 agent worker 测试
- 联调测试结果

---

### 2.4 审计与回放验证
检查是否能仅通过审计重建一次完整 trace。

**通过标准**
- 入口接收、路由决策、执行、结果、取消、失败都有审计
- 审计记录能串起完整生命周期
- 能基于审计数据重建关键事件序列

**证据要求**
- JSONL 审计样本
- 回放验证结果
- 端到端 trace 片段

---

## 3. 分阶段验证清单

### Phase 10.1 — 架构与契约设计验证

**验证内容**
- 主 agent / 子 agent 职责边界是否清晰
- 统一请求模型是否覆盖 CLI / HTTP / Feishu
- capability 声明是否足够支撑路由
- 通信协议是否包含 request / response / cancel / status / health
- 失败 / 取消 / 超时语义是否完整
- 审计事件覆盖是否完整

**通过标准**
- 设计文档能够回答 review 中的所有边界问题
- 没有“谁负责这个”的模糊点
- 没有未定义的关键消息类型

**证据**
- `phase10-tasks.md`
- `phase10-contracts.md`
- `phase10-schema-sketch.md`
- `phase10-review-checklist.md`
- 设计 review 记录

---

### Phase 10.2 — 契约与测试先行验证

**验证内容**
- 请求模型契约测试
- capability 模型契约测试
- 通信协议契约测试
- 审计字段契约测试
- 取消 / 超时 / 失败语义单元测试

**通过标准**
- 所有契约测试先失败、后通过
- 缺字段 / 非法值 / 结构不一致都会被拒绝
- 测试能清楚说明契约边界

**证据**
- failing test 列表
- green test 结果
- 代码审查记录

---

### Phase 10.3 — 主 agent 核心验证

**验证内容**
- capability-based 路由器是否正确选中子 agent
- registry 是否能正确登记和发现子 agent
- dispatch / return 是否形成闭环
- 审计是否能记录路由 / 分发 / 回传 / 失败 / 取消

**通过标准**
- 正确任务路由到正确子 agent
- 无匹配时进入明确失败路径
- 取消请求能进入统一取消处理
- 审计记录能串起决策链

**证据**
- 主 agent 单元测试
- 路由集成测试
- 审计样本

---

### Phase 10.4 — 子 agent worker 验证

**验证内容**
- 子 agent 是否作为独立进程 / worker 启动
- capability 声明是否可被主 agent 读取
- 单一任务执行是否稳定
- 失败 / 超时 / 取消是否正确上报

**通过标准**
- 子 agent 不依赖主进程共享内存来“假装执行”
- 子 agent 能单独启动、单独结束、单独回传
- 主 agent 能根据返回结果判断成功 / 失败 / 超时

**证据**
- worker 启动日志
- worker 协议测试
- 失败 / 超时 / 取消测试

---

### Phase 10.5 — 入口统一与联调验证

**验证内容**
- CLI 是否统一进入主 agent
- HTTP 是否统一进入主 agent
- Feishu 是否统一进入主 agent
- 主 agent 是否再路由到子 agent
- 端到端 trace 是否完整

**通过标准**
- 三种入口都不绕过主 agent
- 三种入口都能得到一致的内部请求模型
- 最终结果能正常回传

**证据**
- CLI / HTTP / Feishu 联调记录
- 端到端 trace
- 审计样本

---

### Phase 10.6 — 收尾与 review 验证

**验证内容**
- `validation.md` 是否写入阶段证据
- `tasks.md` 是否反映最新状态
- review 总结是否足够支持下一阶段推进

**通过标准**
- Phase 10 的设计、契约、实现、验证都能被 review
- 下一个阶段入口清晰

**证据**
- validation 记录
- tasks 状态更新
- review summary

---

## 4. Evidence 格式模板

每条阶段证据建议包含：

- **Evidence #N**
- 日期时间（UTC）
- 验证对象（设计 / 契约 / 路由 / worker / 联调 / 审计）
- 执行命令或 review 动作
- 结果摘要
- 发现的问题 / 风险
- 下一步处理建议

### 建议模板

```text
Evidence #N
UTC: YYYY-MM-DDTHH:MM:SSZ
Scope: <design | contract | routing | worker | integration | audit>
Action: <review / command / test / demo>
Result: <pass / fail / partial>
Notes: <summary of observations>
Risks: <any open issues>
Next: <next action>
```

---

## 5. 推荐的验证顺序

1. 先确认 Phase 10 设计文档 review 通过
2. 再确认契约草案完整
3. 再写契约测试
4. 再写主 agent 路由与调度
5. 再写子 agent worker
6. 再做入口统一接入
7. 最后做端到端联调与审计回放

---

## 6. 通过定义

Phase 10 只有在以下条件同时满足时才算通过：

- 设计清晰
- 契约完整
- 测试先行
- 主 agent 能路由
- 子 agent 真独立进程运行
- CLI / HTTP / Feishu 统一入口
- 审计可回放
- 取消 / 超时 / 失败可复现且可追踪

---

## 7. 备注

这份文档是 Phase 10 的验证草案，后续应在实现推进过程中补充具体 Evidence #N 记录，并与 `tasks.md` 保持同步。
