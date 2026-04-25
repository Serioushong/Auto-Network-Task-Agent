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

> ⚠️ **Phase 6 MVP 限制**：`IdempotencyCache` 目前是 `KernelHarness` 实例内的内存态；
> 每次 `orchestrator-kernel submit` 都会启动一个全新的子进程 → 全新 cache，
> 因此"同一 eventId 跨多个 CLI 进程"无法命中 replay。**进程内幂等**（同一 KernelHarness
> 收到两次 submit）已由 `tests/integration/test_p2_*` 17/17 绿灯覆盖；
> **跨进程幂等**需要 Phase 7 T084 daemon mode 暴露本地 socket 端点，届时所有 CLI
> 命令都会连到常驻守护进程共享同一个 cache。
>
> 下方命令目前会返回 **5 个不同的 traceId**，属于已知架构缺口；当 Phase 7 上线后，
> 相同命令会按预期返回同一 traceId。

```powershell
# 进程内幂等（pytest 场景；见 tests/integration/test_p2_idempotency.py）
# 跨进程演示 (等 Phase 7 daemon mode 上线后才会按预期命中 replay)
1..5 | ForEach-Object {
    orchestrator-kernel submit `
        --event-id "01HYZDEMOSTABLE000000000XY" `
        --text "echo hello again"
}
```

**event-id 格式约束**：长度必须在 16–64 字符之间（`EntryEvent.eventId` 契约，
等同于 ULID-26 规格）；低于 16 字符 CLI 会以退出码 2 + 单行错误消息友好拒绝，
不再抛出 pydantic traceback。省略 `--event-id` 时内核会自动为这次 submit 生成一个 ULID-26。

预期（Phase 7 daemon mode 上线后）：5 次返回同一 traceId；审计里 4 条 `idempotent_replay=true`；stub echo Worker 只被调用 1 次。

---

## 3. 演练 —— HIGH_RISK 审批门 (P3)

> ⚠️ **Phase 6 MVP 限制**：`approve` / `deny` 作为独立 CLI 子命令当前是 **stub**
> （退出码 2 + 指向 Phase 7 daemon mode），原因同 §2.4 —— 跨进程无法触达内存中的
> `ApprovalGate`。单进程端到端演练请使用下方 `--auto-approve` / `--auto-deny`
> 标志；跨进程 `approve <traceId>` / `deny <traceId>` 将随 Phase 7 T084 daemon mode
> 一起上线。

### 3.a 单进程端到端：auto-approve / auto-deny（**推荐，已可跑**）

```powershell
# 批准路径
orchestrator-kernel submit `
    --text "delete fake-file.txt" `
    --worker src/workers_stub/danger_worker.py `
    --auto-approve `
    --approval-timeout-ms 3000
# 预期: traceOutcome=all_succeeded, message="would-delete fake-file.txt"

# 拒绝路径
orchestrator-kernel submit `
    --text "delete fake-file.txt" `
    --worker src/workers_stub/danger_worker.py `
    --auto-deny `
    --approval-timeout-ms 3000
# 预期: traceOutcome=denied, failureReason=user_rejected

# 超时路径（400ms 窗口，不回应）
orchestrator-kernel submit `
    --text "delete fake-file.txt" `
    --worker src/workers_stub/danger_worker.py `
    --approval-timeout-ms 400
# 预期: traceOutcome=denied, leafOutcome=denied_by_timeout,
#        failureReason=approval_timeout, duration≈0.4s
```

审计文件应出现：`task_pending_approval` → (auto-approve → `approval_granted` → `task_dispatched` → `task_succeeded`) 或 (auto-deny → `approval_denied` → leaf `denied`) 或 (超时 → `approval_timeout` → leaf `denied_by_timeout`)。

### 3.b 跨进程 approve/deny（**待 Phase 7 daemon mode**）

```powershell
# 这些命令当前是 stub，只会退出码 2 + 提示 Phase 7
orchestrator-kernel approve <traceId>
orchestrator-kernel deny <traceId>
```

---

## 4. 演练 —— 一键取消 (P4)

> ⚠️ **Phase 6 MVP 限制**：`orchestrator-kernel cancel <traceId>` 作为独立 CLI
> 子命令当前同样是 stub（原因同 §3.b）。cancel 的完整状态机已完全落地，被
> `tests/integration/test_p4_cancel.py` + `tests/integration/test_cancel_approval_race.py`
> + `tests/unit/test_cancel_signal_escalation.py` 13/13 用例严格覆盖（FR-013 ≤ 5s
> 刹车、FR-014 3s 软→1s 硬升级、重复 cancel、approve-vs-cancel 竞态）。
> 跨进程 CLI cancel 将随 Phase 7 T084 daemon mode 一起上线。

### 4.a 单进程 sleep 成功路径（当前可跑）

```powershell
# sleep 0.5 秒然后成功返回
orchestrator-kernel submit `
    --text "sleep 0.5" `
    --worker src/workers_stub/sleep_worker.py
# 预期: traceOutcome=all_succeeded, message="slept 0.50s", duration≈0.51s
```

### 4.b 跨进程 cancel（**待 Phase 7 daemon mode**）

```powershell
# 触发一个慢动作
orchestrator-kernel submit `
    --text "sleep 30" `
    --worker src/workers_stub/sleep_worker.py
# Phase 7 daemon mode 上线后，记下 traceId 然后立即：
orchestrator-kernel cancel <traceId>
```

预期（Phase 7 daemon mode 上线后）：
- ≤ 5 秒内 CLI 前台出现 `traceOutcome=cancelled` 的 ResultSummary；
- 审计按序：`cancel_requested` → `soft_abort_sent` → （若 Worker 及时响应）`task_cancelled`；
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
