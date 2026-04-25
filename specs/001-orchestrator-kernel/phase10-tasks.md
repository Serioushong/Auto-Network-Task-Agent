# Tasks: Phase 10 — 主 Agent / 子 Agent 协作体系（Draft）

**Branch**: `hjx`（按 Constitution Article VIII）
**Created**: 2026-04-25
**Inputs**: [`spec.md`](./spec.md) • [`plan.md`](./plan.md) • [`research.md`](./research.md) • [`data-model.md`](./data-model.md) • [`contracts/`](./contracts/) • [`quickstart.md`](./quickstart.md) • [`../../.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Phase 10 子阶段标记，便于 review；不按 US 编号映射
- 所有路径均为 repo-relative；`src/orchestrator_kernel/` 与 `tests/` 在仓库根

## Tests are REQUIRED（宪法 Article VIII）

Phase 10 仍然严格遵守 **红 → 绿 → 重构**。先定契约与 failing tests，再写实现。任何跨边界通信都必须先有 schema / pydantic 模型，再有代码。

---

## Phase 10.1: 架构与契约设计

**Purpose**: 明确主 agent / 子 agent 职责边界、通信协议、审计字段、失败语义。

- [ ] P10-T01 定义主 agent / 子 agent 职责边界说明
  - 输出：控制面 / 执行面职责清单
  - 验收：边界无重叠、无歧义

- [ ] P10-T02 定义统一请求模型
  - 包括 `text`、`userId`、`eventId`、`sourceChannel`
  - 验收：CLI / HTTP / Feishu 三种入口都可映射到同一模型

- [ ] P10-T03 定义子 agent capability 声明模型
  - 单 capability、单职责、独立 worker
  - 验收：能力可枚举、可审计、可路由

- [ ] P10-T04 定义主 / 子 agent 通信协议
  - 包括 request / response / cancel / status / health
  - 验收：协议可序列化、可回放、可审计

- [ ] P10-T05 定义失败 / 超时 / 取消语义
  - 包括主 agent 与子 agent 的责任分界
  - 验收：每种异常都有结构化结果

- [ ] P10-T06 定义审计事件覆盖表
  - 覆盖入口接收、路由、执行、结果、取消、失败、重放
  - 验收：能完整复原一次 trace

---

## Phase 10.2: 契约与测试先行

**Purpose**: 先写 failing tests，再写实现，保证协议先定下来。

- [ ] P10-T07 为主 / 子 agent 请求模型写契约测试
  - 验收：正例通过，缺字段 / 非法值拒绝

- [ ] P10-T08 为 capability 声明模型写契约测试
  - 验收：单能力限制、字段校验、序列化一致

- [ ] P10-T09 为 agent 通信协议写契约测试
  - 验收：request / response / cancel / health 协议一致

- [ ] P10-T10 为审计字段写契约测试
  - 验收：关键节点都能落 JSONL 审计

- [ ] P10-T11 为取消 / 超时 / 失败语义写单元测试
  - 验收：每个语义都有可重复的测试案例

---

## Phase 10.3: 主 agent 核心实现

**Purpose**: 让主 agent 接收统一请求，并按 capability 路由到子 agent。

- [ ] P10-T12 实现主 agent 路由器
  - 基于 capability-based 规则匹配
  - 验收：能选出正确子 agent

- [ ] P10-T13 实现子 agent registry
  - 负责注册、发现、健康状态维护
  - 验收：注册后可被路由器查询

- [ ] P10-T14 实现主 agent 调度器
  - 负责下发任务、等待回传、处理失败
  - 验收：能完成一次完整 dispatch / return

- [ ] P10-T15 实现主 agent 审计接入
  - 记录路由决策、分发、回传、失败、取消
  - 验收：审计链完整且可回放

- [ ] P10-T16 实现主 agent 取消处理
  - 统一接收取消请求并转发给正在执行的子 agent
  - 验收：取消路径可终止执行并落审计

---

## Phase 10.4: 子 agent 独立 worker 实现

**Purpose**: 子 agent 作为独立进程 worker 真正执行单一任务。

- [ ] P10-T17 实现子 agent worker 入口
  - 独立进程启动
  - 验收：可单独启动并注册

- [ ] P10-T18 实现子 agent capability 声明
  - 单 capability、单职责
  - 验收：主 agent 可读取并路由

- [ ] P10-T19 实现子 agent 任务执行闭环
  - 接收任务、执行、返回结构化结果
  - 验收：能完成一次最小执行

- [ ] P10-T20 实现子 agent 失败上报
  - 失败原因结构化回传
  - 验收：主 agent 可识别失败并审计

- [ ] P10-T21 实现子 agent 超时 / 取消响应
  - 验收：主 agent 可回收卡死 worker

---

## Phase 10.5: 入口统一接入与联调

**Purpose**: CLI / HTTP / Feishu 三个入口统一进入主 agent，再由主 agent 调子 agent。

- [ ] P10-T22 将 CLI 入口接入主 agent
  - 验收：CLI 请求走主 agent 路由

- [ ] P10-T23 将 HTTP 入口接入主 agent
  - 验收：HTTP 请求走主 agent 路由

- [ ] P10-T24 将 Feishu 入口接入主 agent
  - 验收：Feishu 请求走主 agent 路由

- [ ] P10-T25 端到端联调主 / 子 agent 闭环
  - 验收：入口 → 主 agent → 子 agent → 回传 全链路通

- [ ] P10-T26 验证审计可回放
  - 验收：能仅凭审计重建一次完整 trace

- [ ] P10-T27 验证取消 / 超时 / 失败联动
  - 验收：各类异常都能正确收束

---

## Phase 10.6: 收尾与 review

**Purpose**: 将第二阶段结果整理成可 review、可维护的状态。

- [ ] P10-T28 更新 `validation.md` 记录阶段证据
  - 验收：每个关键里程碑都有 evidence

- [ ] P10-T29 更新 `tasks.md` / phase note
  - 验收：阶段状态清晰，下一阶段入口明确

- [ ] P10-T30 形成阶段 review 总结
  - 验收：可直接作为下一轮 review 输入

---

## 依赖顺序

1. 先完成 Phase 10.1 设计与契约
2. 再完成 Phase 10.2 契约测试
3. 再实现主 agent 核心
4. 再实现子 agent worker
5. 最后做入口统一接入与联调

---

## 备注

- 第一版子 agent 只允许单能力，不做多 capability。
- 第一版子 agent 必须是独立进程 / worker。
- 路由方式第一版固定使用 capability-based 规则匹配。
- 第二阶段最优先目标是审计全链路与可回放。
- 该文件为 Phase 10 草案，待 review 后再纳入正式任务流。
