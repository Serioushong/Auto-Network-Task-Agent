# Quickstart — Orchestrator Kernel MVP

**Feature**: 001-orchestrator-kernel
**Target audience**: 开发者 / TDD 执行者 / Alpha 内测用户
**Time budget**: 首次环境 ≤ 10 分钟；后续单次端到端演练 ≤ 3 分钟（SC-001）

> ⚠️ 本阶段 spec-kit 流程未到 `/speckit-implement`，
> **代码目录 `src/` 尚未存在**。本文档描述的是**进入实现阶段后**的开发者体验。
> 当前仓库只有 spec / plan / data-model / contracts。

---

## 0. 前置条件

| 必需 | 版本 | 用途 |
|---|---|---|
| Windows 10+ | PowerShell 5.1 | 宪法 Additional Constraints 硬性要求 |
| Python | 3.11+ | 运行内核与 Worker |
| uv | 最新 | 包管理与虚拟环境；`pip install uv` 或 `irm https://astral.sh/uv/install.ps1 \| iex` |
| Git | 2.40+ | 宪法 VIII 双分支制，日常工作于 `hjx` |

**验证**：

```powershell
python --version    # expect 3.11+
uv --version
git --version
git branch --show-current   # expect hjx
```

---

## 1. 首次环境搭建（≤ 10 分钟）

```powershell
# 1) 克隆（若尚未）
git clone https://github.com/Serioushong/Auto-Network-Task-Agent.git Spec-Plan-Harness
cd Spec-Plan-Harness
git checkout hjx

# 2) 同步依赖（uv 会读取 pyproject.toml 并创建 .venv）
uv sync

# 3) 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 4) 初始化运行期目录
New-Item -ItemType Directory -Force var\audit | Out-Null
New-Item -ItemType Directory -Force var\tmp   | Out-Null

# 5) 运行一次全量测试，确认环境 OK
pytest -q
```

预期输出：所有 `tests/contract/` + `tests/unit/` + `tests/integration/` 绿灯。

---

## 2. 端到端演练 —— P1 基本闭环 (≤ 3 分钟)

> 对应 spec.md User Story 1 + SC-001 / SC-002。

### 2.1 启动内核

新开一个 PowerShell 终端：

```powershell
.\.venv\Scripts\Activate.ps1
orchestrator-kernel start --config .\var\kernel.toml
```

内核启动后会：
1. 扫描 `./var/audit/` 最近一份 JSONL（首次无文件则跳过），补写 `kernel_restart_detected`；
2. 加载 stub Workers：`echo-stub`, `sleep-stub`, `crash-stub`, `danger-stub`；
3. 打印 `ready`，监听 CLI 入口；
4. 审计文件出现 `./var/audit/2026-04-21.jsonl`，第一行 `worker_registered` × 4。

### 2.2 提交第一条指令

另一个终端：

```powershell
.\.venv\Scripts\Activate.ps1
orchestrator-kernel submit --text "echo hello world"
```

预期输出（CLI 前台展示，同时推送 ResultSummary）：

```json
{
  "traceId": "01HYZ...",
  "status": "terminal",
  "traceOutcome": "all_succeeded",
  "leafResults": [
    {"taskId": "01HYZ...", "capability": "echo.say", "outcome": "succeeded"}
  ],
  "message": "All leaf tasks succeeded."
}
```

### 2.3 查看审计

```powershell
Get-Content .\var\audit\$(Get-Date -Format yyyy-MM-dd).jsonl | Select-Object -Last 10
```

应看到按顺序：`event_received` → `trace_created` → `task_created` → `task_dispatched` → `task_started` → `task_succeeded` → `result_summary_prepared` → `result_summary_delivered`。

### 2.4 验证幂等

```powershell
# 重复投递同 eventId 5 次
1..5 | ForEach-Object {
    orchestrator-kernel submit --event-id E-DEMO-1 --text "echo hello again"
}
```

预期：5 次返回同一 traceId；审计里 4 条 `idempotent_replay=true`；stub echo Worker 只被调用 1 次。

---

## 3. 演练 —— HIGH_RISK 审批门 (P3)

```powershell
# 提交 HIGH_RISK 动作
orchestrator-kernel submit --text "delete fake-file.txt"
```

预期：
- CLI 前台收到 `approval_request` 消息，包含 `traceId` 与 `expiresAt`（10 分钟后）；
- Task 处于 `pending_approval`，**未**分派；
- 审计出现 `task_pending_approval`。

三种分支：

```powershell
# (a) 批准
orchestrator-kernel approve <traceId>
# 预期：approval_granted → task_dispatched → ... → task_succeeded

# (b) 拒绝
orchestrator-kernel deny <traceId>
# 预期：approval_denied → task state = denied(user_rejected)

# (c) 超时（等 10 分钟不响应）
# 预期：approval_timeout → task state = denied_by_timeout → ResultSummary 投递
```

---

## 4. 演练 —— 一键取消 (P4)

```powershell
# 触发一个慢动作
orchestrator-kernel submit --text "sleep 30"
# 记下返回的 traceId，立即：
orchestrator-kernel cancel <traceId>
```

预期：
- ≤ 5 秒内 CLI 前台出现 `traceOutcome=cancelled` 的 ResultSummary；
- 审计按序：`cancel_requested` → `soft_abort_sent` → （若 Worker 及时响应）`worker_terminated` → `task_cancelled`；
- 若 Worker 不理会，3 秒后升级：`hard_abort_sent` → `task_cancelled(hard_terminated)`。

---

## 5. 演练 —— 崩溃隔离 (P5) + 崩溃恢复 (FR-028)

### 5.1 Worker 崩溃

```powershell
# 同时派发两条 trace，其中一条使 crash-stub 崩溃
orchestrator-kernel submit --text "normal echo A" &
orchestrator-kernel submit --text "crash now B"
```

预期：
- trace B → `failed(worker_crashed)`；
- trace A → 正常 `succeeded`；
- 内核主循环未重启（pid 不变）。

### 5.2 内核崩溃

```powershell
# 在 sleep-stub 运行期间强制 kill 内核
orchestrator-kernel submit --text "sleep 60" > $null
Stop-Process -Name orchestrator-kernel -Force

# 重启
orchestrator-kernel start --config .\var\kernel.toml
```

预期：
- 内核启动阶段 ≤ 10 秒内扫描审计日志（SC-009）；
- 对刚才未终态的 Task 补写 `in_flight_auto_failed(kernel_restart)`；
- 对每个受影响 trace 推送 ResultSummary 到原 sourceChannel，message 含 "system interrupted; re-submit with a NEW eventId"；
- 重启扫描期间 CLI 再次 `submit` 会收到 `rejected(reason=kernel_warming_up)`。

---

## 6. 开发者循环（TDD）

按宪法 Article VIII，实现阶段必须走红-绿-重构：

```powershell
# 1) 先写失败测试
pytest tests/integration/test_p1_basic_loop.py::test_echo_succeeds -x   # 期望红

# 2) 写最小实现让它变绿
#   ...编辑 src/...

# 3) 全量绿
pytest -q

# 4) 提交到 hjx
git add .
git commit -F (给出更新说明的临时文件)
git push origin hjx
```

**合并到 main 的门禁**（Constitution Article VIII）：
1. `/speckit-analyze` 通过
2. 所有 contract / integration / unit 测试绿
3. 至少一次人工端到端演练（本文档 §2~§5 走一遍）
4. 打 SemVer tag（如 `v0.1.0`）

---

## 7. 常见问题

### Q: `pytest` 报 "no module named orchestrator_kernel"
A: 确认 `uv sync` 已执行并激活 `.venv`；再次检查 `pyproject.toml` 的 `[tool.uv.sources]` 是否指向本地 package。

### Q: 审计文件写入失败
A: 检查 `./var/audit/` 是否存在且可写；按 SC-008，内核应在 10 秒内切入"拒绝新入口事件"并给出 `disk_write_failed` 审计（磁盘恢复后可重新写入）。

### Q: Worker 进程不退出
A: 先发 `cancel <traceId>`；3 秒后内核自动硬终止。若内核也卡死，`Stop-Process -Name orchestrator-kernel`，然后重启内核会触发崩溃恢复流程（§5.2）。

### Q: 我想接飞书
A: 这是 002 feature 的范围；MVP 的 `feishu_stub` 只是打印占位，不做真实 IM 对接。

---

## 8. 相关文档

- [spec.md](./spec.md) —— 功能规范（what & why）
- [plan.md](./plan.md) —— 本 plan（how）
- [research.md](./research.md) —— 技术决策与替代方案
- [data-model.md](./data-model.md) —— 实体 / 状态机 / 不变量
- [contracts/](./contracts/) —— 8 份 JSON Schema + README
- [../../.specify/memory/constitution.md](../../.specify/memory/constitution.md) —— 项目宪法 v1.0.0
