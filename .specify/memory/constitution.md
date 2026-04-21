<!--
Sync Impact Report
==================
Version change: (initial) → 1.0.0
Modified principles: N/A (first ratification)
Added sections:
  - Core Principles (8 principles)
  - Additional Constraints
  - Development Workflow & Quality Gates
  - Governance
Removed sections: none
Templates requiring updates:
  - .specify/templates/plan-template.md  ✅ updated (Constitution Check gates populated)
  - .specify/templates/spec-template.md  ✅ no change needed (feature spec scope)
  - .specify/templates/tasks-template.md ✅ no change needed (task taxonomy unaffected)
  - .specify/templates/checklist-template.md ✅ no change needed
  - .cursor/rules/specify-rules.mdc      ✅ no change needed (generic guidance)
Follow-up TODOs: none
-->

# Spec-Plan-Harness Constitution

> 飞书下发命令 → 主 Agent 编排 → 子 Agent 操控电脑 的多 Agent 自动化协同办公系统宪法。
> 本宪法效力高于 plan.md、tasks.md 与任何实现细节；违反条款的变更必须走宪法修订流程。

## Core Principles

### I. Spec-Driven Development (NON-NEGOTIABLE)

任何功能在进入 `/speckit-implement` 前 **MUST** 依序完成
`/speckit-constitution → /speckit-specify → /speckit-clarify → /speckit-plan →
/speckit-tasks → /speckit-analyze`，且 `/speckit-analyze` 必须通过。
未经此流程产生的代码禁止合入 `main`。

*Rationale*: 多 Agent 系统的失败几乎都来自边界模糊的需求与隐式契约，
强制规范驱动流程把"意图"显式化为可审计工件，避免 AI 与人一起即兴发挥。

### II. Least Privilege (Capability Matrix)

主 Agent 与任一子 Agent **MUST** 仅能使用在该功能 spec 的 "Capability Matrix"
中显式声明的能力；未声明能力一律默认禁用。
"不可逆动作"——包括但不限于：文件删除、系统配置修改、密码/令牌读写、
支付/财务、对外发送消息、网络爬取——**MUST** 在 spec 中单独标注为
`HIGH_RISK` 并走 Principle III 的人机协同门。

*Rationale*: 防止 LLM 幻觉或 prompt 注入触发超出授权范围的副作用。

### III. Human-in-the-Loop Gate

所有 `HIGH_RISK` 动作 **MUST** 在执行前向人类发送确认请求，
默认确认超时视为拒绝且必须落盘为 `denied_by_timeout` 事件。
主 Agent **MUST** 内建"一键取消全部正在运行任务"的能力，
取消必须在 ≤ 5 秒内对所有子 Agent 生效（含进程终止 + 状态落盘）。

*Rationale*: 自动化系统的用户信任直接取决于"能否刹住车"。

### IV. Observability & Auditability

系统 **MUST** 为每条 "指令 → 任务 → 子 Agent 行为 → 结果" 链路生成
结构化 JSONL 日志，至少包含：`traceId`、`taskId`、`parentTaskId`、
`actor`、`capability`、`input_hash`、`output_hash`、`timestamp`、`outcome`。
敏感字段 **MUST** 脱敏（hash 或 mask）。
任意 `traceId` **MUST** 可重建完整决策链，保留期 ≥ 30 天。

*Rationale*: 没有审计日志的 Agent 系统 = 不可调试 + 不可问责。

### V. Sandboxed Execution

子 Agent **MUST** 运行在独立进程或容器中，具备：
CPU 使用率上限、内存上限、最长执行时长、网络 allowlist（默认 deny-all）。
任一子 Agent 崩溃或超限 **MUST NOT** 影响主 Agent、
其他子 Agent 或宿主系统的运行。
桌面操控类子 Agent 额外 **MUST** 支持"软中止信号"
（优先清理当前操作而非强制 kill）。

*Rationale*: 操控电脑的 Agent 一旦失控后果比普通后端服务严重得多。

### VI. Idempotent & Replayable

入口事件（飞书消息、CLI、HTTP）的重复投递 **MUST NOT** 产生重复副作用：
每条任务 **MUST** 具备稳定 `taskId`，其派发与执行层需做幂等去重。
任何任务 **MUST** 可按 `traceId` 重放到"动作请求前"的状态
（只读重放），用于事故回溯与测试。

*Rationale*: 消息队列重复投递、用户重复下发、崩溃后恢复都必须是安全操作。

### VII. Contract-First

主 Agent ↔ 子 Agent、系统 ↔ 外部入口（飞书 / CLI / HTTP / Webhook）
之间的所有消息 **MUST** 先以机器可校验的 Schema
（JSON Schema 或 Protobuf 或 Pydantic/Zod 等等价工具）定义，
再写实现。契约变更 **MUST** 触发 `CONSTITUTION_VERSION` MAJOR 或
spec MAJOR 版本变更，且必须经 `/speckit-analyze` 审计通过。

*Rationale*: 多 Agent 系统本质是分布式系统，契约不先行就是在沙滩上盖楼。

### VIII. Branch-Based Dual-Gate Release (NON-NEGOTIABLE)

仓库 **MUST** 维持两条长期分支：
- `main` —— 生产/可信分支。**MUST NOT** 被直接 push；**MUST NOT** 被 force-push。
- `hjx`  —— 开发/集成分支。所有开发、实验、spec 迭代 **MUST** 先提交到此。

每次向 `hjx` 的提交或 PR **MUST** 在描述中包含"更新说明"，至少覆盖：
（a）变更点，（b）影响范围，（c）测试结论或"暂未测试"的原因。

从 `hjx` 合并入 `main` **MUST** 满足以下全部条件：
1. 该功能的 Spec Kit 全流程完成，`/speckit-analyze` 通过；
2. TDD 红-绿-重构循环走完，所有自动化测试（单元 + 集成 + 契约）通过；
3. 至少一次人工验收演练（端到端跑通飞书侧或桌面操控侧其一）；
4. 合并后打 SemVer tag（如 `v0.3.0`）。

合并方式优先 fast-forward 或 merge commit（允许 squash，但 **MUST** 保留
原始 commit 链接或摘要在合并说明中）。

*Rationale*: 双分支 + 强门槛是把"看起来能跑"和"真的能上线"分开的最低代价。

## Additional Constraints

- **平台**: 本阶段必须在 Windows + PowerShell 5.1 环境可完整运行；
  桌面操控子 Agent 至少支持 Windows。Linux/macOS 支持视后续 spec 决定。
- **技术栈**: 具体语言、消息总线、LLM 供应商、桌面操控 SDK 的选型延后到
  `/speckit-plan`；但宪法硬性要求"可替换的 LLM 抽象层"，
  **MUST NOT** 在实现中硬编码任一厂商 SDK。
- **成本控制**: 主 Agent **MUST** 为每次任务分派设置 token/时长/工具调用
  次数上限，超限即终止并记录。
- **依赖新引入**: 引入新生产依赖 **MUST** 在 PR 描述中声明授权证书与维护
  活跃度评估。

## Development Workflow & Quality Gates

1. 所有日常工作默认 `git checkout hjx`。
2. 功能启动时，先运行 `/speckit-specify` 生成 `specs/NNN-xxx/spec.md`，
   并在 `hjx` 上以原子提交落盘。
3. `/speckit-plan` 生成的 plan 必须通过 `## Constitution Check` 的全部八条
   门槛，未通过的必须在 `## Complexity Tracking` 里给出豁免理由与更简方案
   被否决的原因。
4. `/speckit-tasks` 产出的任务 **MUST** 明确标注 TDD 顺序：
   "先写失败测试 → 再写最小实现 → 再重构"。
5. CI（未来建立）**MUST** 阻止未经 `/speckit-analyze` 通过的 PR 合入 `main`。

## Governance

- **版本**: 宪法采用 SemVer。
  - MAJOR: 原则被删除、重定义或兼容性破坏性变更；
  - MINOR: 新增原则/章节或实质性扩展；
  - PATCH: 措辞、错别字、澄清等非语义修订。
- **修订**: 任何宪法修订 **MUST** 以 PR 形式提交，在 PR 描述里包含
  "迁移说明"与受影响工件清单；合并前必须在 `hjx` 上至少存在一次
  `/speckit-analyze` 通过的演练。
- **优先级**: 宪法 > plan.md > tasks.md > 实现代码；冲突以宪法为准。
- **合规审查**: 每次 `/speckit-plan` 与 `/speckit-analyze` **MUST** 将八条
  原则作为显式 gate 检查并在输出中报告 PASS/FAIL。

**Version**: 1.0.0 | **Ratified**: 2026-04-21 | **Last Amended**: 2026-04-21
