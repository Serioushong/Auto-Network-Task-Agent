# Tasks: Orchestrator Kernel MVP (001-orchestrator-kernel)

**Branch**: `hjx` (per Constitution Article VIII)
**Created**: 2026-04-21
**Inputs**: [spec.md](./spec.md) • [plan.md](./plan.md) • [research.md](./research.md) • [data-model.md](./data-model.md) • [contracts/](./contracts/) • [quickstart.md](./quickstart.md) • [../../.specify/memory/constitution.md](../../.specify/memory/constitution.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: `US1..US7` maps to user stories / extension groups; Setup / Foundational / Polish 不带 Story 标
- 所有路径均为 repo-relative；`src/orchestrator_kernel/` 与 `tests/` 在仓库根

## Tests are REQUIRED (Constitution Article VIII)

宪法 Article VIII 钉死 TDD **红 → 绿 → 重构** 节奏；每条 `[US*]` 的 test 任务 MUST 先写且先失败，
再做实现让其变绿。本 tasks.md 全文遵守该顺序，测试任务与实现任务已按依赖关系排列。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Python 工程初始化、依赖、测试骨架。

- [ ] T001 Create `pyproject.toml` at repo root with `[project]` metadata, `requires-python = ">=3.11"`, and runtime deps (`pydantic>=2.7`, `anyio>=4`, `structlog>=24`, `typer>=0.12`, `psutil>=5.9`, `jsonschema>=4.21`, `ulid-py>=1.1`) + dev deps (`pytest>=8`, `pytest-asyncio>=0.23`, `hypothesis>=6`, `ruff>=0.4`, `mypy>=1.10`). Register console script `orchestrator-kernel = "orchestrator_kernel.cli_main:app"`.
- [ ] T002 Scaffold `src/orchestrator_kernel/` package tree per `plan.md` §Project Structure. Create `__init__.py` in each subpackage (`contracts/`, `entrypoints/`, `kernel/`, `worker_supervisor/`, `audit/`, `notifier/`, `llm/`). Create `src/workers_stub/` with `__init__.py`.
- [ ] T003 [P] Scaffold `tests/` tree: `tests/contract/`, `tests/integration/`, `tests/unit/`, each with `__init__.py` and a root `conftest.py` that yields a `tmp_audit_dir` fixture (uses `tmp_path`).
- [ ] T004 [P] Configure quality tooling in `pyproject.toml`: `[tool.ruff]` (select E,F,I,W,UP,B; line-length 100); `[tool.mypy]` (strict = true; files = src); `[tool.pytest.ini_options]` (asyncio_mode = "auto"; addopts = "-ra"); `[tool.hypothesis]` (deadline = 2000).
- [ ] T005 [P] Extend `.gitignore` with `var/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `dist/`, `*.egg-info/`.
- [ ] T006 Run `uv sync` and confirm `pytest -q` executes (empty suite passes). Record command in `specs/001-orchestrator-kernel/validation.md` as first evidence line.

**Checkpoint**: `uv run pytest -q` 绿灯（0 条测试，0 错误）。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 契约层（9 份 pydantic 镜像 + 对应 JSON Schema 双向校验）、审计基础设施、状态机、配置、输入护栏（payload size + 限流）。**任一 user story 均依赖本阶段完成。**

**⚠️ CRITICAL**: User story 工作 MUST NOT 在本 Phase 完成前启动。

### 2A · Contract tests (RED — all [P], one file each)

- [ ] T007 [P] Write failing contract test in `tests/contract/test_entry_event.py`: 正例合规；反例覆盖缺字段、`text > 16_384 bytes`、非法 `sourceChannel`、`eventId` 长度越界。引用 `specs/001-orchestrator-kernel/contracts/entry-event.schema.json`。
- [ ] T008 [P] Write failing contract test in `tests/contract/test_task.py`: root_intent vs leaf_action 分支；leaf 缺 `capability`/`riskLevel`/`budget` 应拒；state/outcome 交叉一致性；`resultHash` pattern。
- [ ] T009 [P] Write failing contract test in `tests/contract/test_budget.py`: 三维边界（min=1、硬顶 1_800_000 / 200 / 500_000）；越界应拒。
- [ ] T010 [P] Write failing contract test in `tests/contract/test_worker_registration.py`: capability name pattern、riskLevel 枚举、`resourceLimits` 范围；capability 超硬顶 budget 注册应拒。
- [ ] T011 [P] Write failing contract test in `tests/contract/test_worker_protocol.py`: 覆盖全部 7 种帧（register/dispatch/started/result/heartbeat/abort/shutdown）的正反例；discriminator `kind` 错误应拒。
- [ ] T012 [P] Write failing contract test in `tests/contract/test_approval.py`: request 与 response 两支；userId 长度、decision 枚举、expiresAt 必填。
- [ ] T013 [P] Write failing contract test in `tests/contract/test_cancel.py`: 必需字段、userId 越界拒绝。
- [ ] T014 [P] Write failing contract test in `tests/contract/test_audit_event.py`: 全部 36 种 `eventType` 枚举断言；`actor` pattern；`input_hash`/`output_hash` pattern；nullable `traceId` 路径。
- [ ] T015 [P] Write failing contract test in `tests/contract/test_result_summary.py`: `traceOutcome` 枚举、`leafResults` items schema、`deliveryAttempt` ≤ 3；`kernel_restarted` 时 `message` 必须含重投提示。

### 2B · Pydantic mirrors (GREEN — all [P], one file each)

- [ ] T016 [P] Implement `EntryEvent` in `src/orchestrator_kernel/contracts/entry_event.py` (pydantic v2；`model_config = ConfigDict(extra="forbid")`；`text` 长度校验器按 UTF-8 字节数计算)。
- [ ] T017 [P] Implement `Task` / `TaskState` / `TaskOutcome` / `FailureReason` enums + model in `src/orchestrator_kernel/contracts/task.py`；嵌套 leaf-specific 校验（pydantic `model_validator(mode="after")`）。
- [ ] T018 [P] Implement `Budget` + `DEFAULT_BUDGET` + `SYSTEM_HARD_CAP` constants in `src/orchestrator_kernel/contracts/budget.py`.
- [ ] T019 [P] Implement `Capability`, `ResourceLimits`, `WorkerRegistration` in `src/orchestrator_kernel/contracts/worker.py`；在 model_validator 中断言 `capability.budget` 不越硬顶。
- [ ] T020 [P] Implement discriminated union `WorkerFrame` (Register/Dispatch/Started/Result/Heartbeat/Abort/Shutdown) in `src/orchestrator_kernel/contracts/worker_protocol.py` 使用 pydantic `Discriminator("kind")`.
- [ ] T021 [P] Implement `ApprovalRequest`/`ApprovalResponse` + `ApprovalMessage` union in `src/orchestrator_kernel/contracts/approval.py`.
- [ ] T022 [P] Implement `CancelMessage` in `src/orchestrator_kernel/contracts/cancel.py`.
- [ ] T023 [P] Implement `AuditEvent` + `EventType` Literal union in `src/orchestrator_kernel/contracts/audit.py`.
- [ ] T024 [P] Implement `ResultSummary` + `LeafResult` in `src/orchestrator_kernel/contracts/result_summary.py`.
- [ ] T025 Run `pytest tests/contract/ -q`；全部变绿；round-trip（pydantic → `model_json_schema()` → 与 `contracts/*.schema.json` 断言一致）作为 contract test 的内嵌子用例。

### 2C · Shared infra

- [ ] T026 Implement `src/orchestrator_kernel/config.py`: 使用 pydantic-settings 加载 `var/kernel.toml`；暴露 `system_hard_cap`、`approval_timeout_default_ms=600_000`、`payload_max_bytes_default=16_384`、`rate_limit_defaults`；启动时对任何部署配置越界值 raise。
- [ ] T027 [P] Implement hashing + redact helpers in `src/orchestrator_kernel/audit/hasher.py` (SHA-256 截前 16 bytes hex) 与 `src/orchestrator_kernel/audit/redact.py` (structlog processor；按字段名 allowlist + 值长度阈值双策略)。
- [ ] T028 [P] Write failing unit test `tests/unit/test_state_machine.py`: Task.state 单向转换表；`succeeded/failed/cancelled/denied/denied_by_timeout` 为终态（任何出边应 raise）；HIGH_RISK 必经 `pending_approval`。
- [ ] T029 Implement Task state machine in `src/orchestrator_kernel/kernel/state_machine.py`：一个 `transition(task, to_state, *, reason=None) -> Task` 纯函数，内含 INV-2 / INV-3 断言。
- [ ] T030 [P] Write failing unit test `tests/unit/test_audit_writer.py`: JSONL append; UTC 日切 rotation；`disk_write_failed` 事件在磁盘不可写时落盘并切入"拒绝新入口"（通过依赖注入模拟磁盘故障）。
- [ ] T031 Implement audit writer in `src/orchestrator_kernel/audit/writer.py`：结合 structlog JSONL processor、`O_APPEND` 写入、每天 UTC 0 点 rotate；disk-full handling 通过 `IOError` 捕获 + 切状态 + 自审计；暴露 `AuditWriter.write(AuditEvent) -> None` 与 `AuditWriter.healthy: bool`。
- [ ] T032 [P] Write failing unit test `tests/unit/test_payload_size.py`: 正好 16_384 bytes 通过；16_385 bytes 拒；`rejected(reason=payload_too_large, limit=16KB, actual=<bytes>)` 审计事件结构正确。
- [ ] T033 Implement payload-size guard in `src/orchestrator_kernel/kernel/validators.py`：`assert_payload_size(event)` 先于 schema 校验运行（FR-031）。
- [ ] T034 [P] Write failing unit test `tests/unit/test_rate_limit.py`: 覆盖 4 个维度（`global_rps=50`、`user_rpm=120`、`user_concurrent=10`、`user_highrisk_concurrent=1`）；`rejected(reason=rate_limited, dimension=…)` 事件结构。
- [ ] T035 Implement rate limiter in `src/orchestrator_kernel/kernel/rate_limit.py`：token bucket (全局 rps) + sliding window (per-user rpm) + counter (per-user concurrent)；使用 `anyio.Lock` 保护。

**Checkpoint**: `pytest tests/contract/ tests/unit/ -q` 全绿；契约与基础设施就位，user stories 可并行启动。

---

## Phase 3: User Story 1 — 基本分派闭环 (Priority: P1) 🎯 MVP

**Goal**: Alpha 用户通过 CLI 提交一条 `echo hello`，内核创建 Task 树、分派给 `echo-worker`、写审计、回传结果。

**Independent Test**: 按 `quickstart.md §2` 演练；审计可从 `event_received` 追到 `result_summary_delivered`；p95 ≤ 3 s (SC-002)。

### Tests for US1 (RED)

- [ ] T036 [P] [US1] Write failing integration test `tests/integration/test_p1_basic_loop.py`：启动内核 fixture + `echo-worker` stub；通过 CLI 接口提交 `{text: "echo hello"}`；断言 (a) 返回 `traceOutcome=all_succeeded`、(b) 审计链完整（`event_received → trace_created → task_created → task_dispatched → task_started → task_succeeded → result_summary_prepared → result_summary_delivered`）、(c) 端到端耗时 ≤ 3 s。
- [ ] T037 [P] [US1] Write failing integration test `tests/integration/test_p1_no_worker.py`：提交需要 `desktop.click` capability 的事件，无 Worker 声明；断言 `failed(reason=no_capable_worker)` 闭环。
- [ ] T038 [P] [US1] Write failing contract-level integration test `tests/integration/test_worker_stdio_roundtrip.py`：内核 ↔ Worker stdio 一轮 dispatch/started/result；字节层精确匹配 JSON Schema。

### Implementation for US1 (GREEN)

- [ ] T039 [US1] Implement Task tree builder in `src/orchestrator_kernel/kernel/task_tree.py`：`build_from_event(event, capability_plan) -> Task tree`；MVP 支持根 + 单叶。
- [ ] T040 [US1] Implement capability dispatcher in `src/orchestrator_kernel/kernel/dispatcher.py`：按 `(capability, healthy)` 匹配 Worker；无匹配落 `failed(no_capable_worker)`。
- [ ] T041 [US1] Implement subprocess worker supervisor in `src/orchestrator_kernel/worker_supervisor/supervisor.py`：`spawn(worker_script_path, *, creationflags=CREATE_NEW_PROCESS_GROUP)`；绑定 Windows Job Object（占位 API，T074 补全真实限制）。
- [ ] T042 [US1] Implement stdio JSON-lines protocol reader/writer in `src/orchestrator_kernel/worker_supervisor/protocol.py`：异步读 stdout、按行 JSON parse + pydantic `WorkerFrame` 校验；异步写入 dispatch/abort/shutdown 帧。
- [ ] T043 [US1] Implement `echo-worker` stub in `src/workers_stub/echo_worker.py`：一个独立 `python -m` 可执行脚本；启动即发 `register` 帧（capability `echo.say` NORMAL）；收到 `dispatch` 后 50 ms 内回 `started` 再回 `result(succeeded, output.text=payload.text)`。
- [ ] T044 [US1] Implement CLI `submit` command in `src/orchestrator_kernel/entrypoints/cli.py` 使用 typer：参数 `--text`（必需）、`--event-id`（可选自动生成）、`--user-id`（可选从 env 取）、`--source-channel=cli`。
- [ ] T045 [US1] Wire top-level async event loop in `src/orchestrator_kernel/cli_main.py`：装配 config → audit writer → validators → rate limiter → idempotency (stub) → task tree → dispatcher → supervisor → notifier；提供 `app = typer.Typer()` 供 entry_point。
- [ ] T046 [US1] Implement minimal `ResultSummary` 生成 + CLI 前台打印 in `src/orchestrator_kernel/notifier/result_summary.py`（交付完整版留到 US6 T075）。
- [ ] T047 [US1] 跑 `pytest tests/integration/test_p1_* tests/integration/test_worker_stdio_roundtrip.py -q` 全绿；在 `validation.md` 记录端到端 p95 实测值。

**Checkpoint**: US1 独立可用；MVP 基线建立。可在此单独 demo。

---

## Phase 4: User Story 2 — 幂等性保证 (Priority: P2)

**Goal**: 5 次同 eventId 投递 → 1 棵 Task 树 + 4 条 `idempotent_replay` 审计。

**Independent Test**: 按 `quickstart.md §2.4`；hypothesis state-machine 测试验证 INV-1。

### Tests for US2 (RED)

- [ ] T048 [P] [US2] Write failing integration test `tests/integration/test_p2_idempotency.py`：覆盖 P2 的 3 个 Acceptance Scenarios（已完成终态、running 中、已失败三支）。
- [ ] T049 [P] [US2] Write failing hypothesis test `tests/integration/test_p2_idempotency_property.py`：`RuleBasedStateMachine`，rules = {submit, duplicate_submit, wait_terminal}；invariants = INV-1 + audit replay 计数 = 原次数 − 1。
- [ ] T050 [P] [US2] Write failing unit test `tests/unit/test_idempotency_cache.py`：纯函数 `lookup_or_register((userId, eventId)) -> (traceId, is_replay)`；并发 race 不破坏键唯一性（用 `anyio.create_task_group` 压测）。

### Implementation for US2 (GREEN)

- [ ] T051 [US2] Implement idempotency cache in `src/orchestrator_kernel/kernel/idempotency.py`：内存 dict + `anyio.Lock`；hit 时返回首次 traceId 与当前 state snapshot。
- [ ] T052 [US2] Wire idempotency check into `cli_main.py` 事件接入管线（在 payload-size / schema / rate-limit 之后、trace 创建之前）。
- [ ] T053 [US2] 在 audit writer 支持 `idempotent_replay=true` 字段（T031 已接 AuditEvent schema，此处只需使用）。
- [ ] T054 [US2] 跑 `pytest tests/integration/test_p2_* tests/unit/test_idempotency_cache.py -q` 全绿。

**Checkpoint**: US1 + US2 可独立 demo（含"重复投递不再副作用"的演示）。

---

## Phase 5: User Story 3 — HIGH_RISK 审批门 (Priority: P3)

**Goal**: `file.delete` 等 HIGH_RISK capability 进 `pending_approval`；approve / deny / timeout 三分支完整。

**Independent Test**: `quickstart.md §3`。

### Tests for US3 (RED)

- [ ] T055 [P] [US3] Write failing integration test `tests/integration/test_p3_approval_gate.py`：覆盖 spec.md P3 的 4 个 Acceptance Scenarios（approve、deny、timeout、越权 userId）。
- [ ] T056 [P] [US3] Write failing unit test `tests/unit/test_approval_gate.py`：状态转换矩阵 + 到期计时精度（用 `anyio` 的虚拟时钟 / `freezegun`）。

### Implementation for US3 (GREEN)

- [ ] T057 [US3] Implement approval gate in `src/orchestrator_kernel/kernel/approval_gate.py`：HIGH_RISK 叶 Task 进入 `pending_approval`，注册带 `expiresAt` 的 timer；暴露 `handle_response(ApprovalResponse)`；`approval_impersonation_rejected` / `approval_stale` 路径。
- [ ] T058 [US3] Implement `danger-worker` stub in `src/workers_stub/danger_worker.py`：capability `file.delete` HIGH_RISK；收到 dispatch 后不真删除（echo 返回 "would-delete <path>"）。
- [ ] T059 [US3] Add CLI `approve <traceId>` / `deny <traceId>` 子命令 in `src/orchestrator_kernel/entrypoints/cli.py`；构造 `ApprovalResponse` 并经同进程 channel 发回内核（MVP 单机，channel = 本地 socket 或共享内存队列，选前者）。
- [ ] T060 [US3] Wire 审批超时 timer 与 CLI 出入通道；默认 10 分钟可通过 `--approval-timeout-ms` 覆写（config 层已在 T026 就位）。
- [ ] T061 [US3] 跑 `pytest tests/integration/test_p3_* tests/unit/test_approval_gate.py -q` 全绿。

**Checkpoint**: US1~US3 均独立可用；"敢不敢对外放"的门槛到位。

---

## Phase 6: User Story 4 — 一键取消 (Priority: P4)

**Goal**: `cancel <traceId>` ≤ 5 s 内令所有 running / dispatched / pending_approval Task 进入 cancelled；软中止 3 s 未响应升级硬终止。

**Independent Test**: `quickstart.md §4`；SC-004 数值达标。

### Tests for US4 (RED)

- [ ] T062 [P] [US4] Write failing integration test `tests/integration/test_p4_cancel.py`：覆盖 spec.md P4 的 3 个 Acceptance Scenarios + Edge Case"重复 cancel"。
- [ ] T063 [P] [US4] Write failing unit test `tests/unit/test_cancel_signal_escalation.py`：mock subprocess + 计时；断言 0/3/4 秒三级升级时序。
- [ ] T064 [P] [US4] Write failing integration test `tests/integration/test_cancel_approval_race.py`：approve 与 cancel 并发；断言"最后到达者胜"（spec.md Edge Case）。

### Implementation for US4 (GREEN)

- [ ] T065 [US4] Implement cancel orchestrator in `src/orchestrator_kernel/kernel/cancel.py`：`async def cancel_trace(traceId, userId) -> CancelResult`；广播 abort 帧 + 送 `CTRL_BREAK_EVENT`/`SIGTERM`；3 s 后 `terminate()` 升级，再 1 s `kill()`；不存在 / 已终态两分支单独审计。
- [ ] T066 [US4] Implement `sleep-worker` stub in `src/workers_stub/sleep_worker.py`：capability `sleep.wait` NORMAL；payload 含 `seconds`；支持 `SIGBREAK` 捕获并优雅退出（用于"听话 Worker"分支）与一个"装死"开关（用于硬终止分支）。
- [ ] T067 [US4] Add CLI `cancel <traceId>` 子命令；构造 `CancelMessage` 回注内核。
- [ ] T068 [US4] Implement signal escalation in `src/orchestrator_kernel/worker_supervisor/lifecycle.py`：Windows 路径使用 `signal.CTRL_BREAK_EVENT` + `creationflags=CREATE_NEW_PROCESS_GROUP`；POSIX 使用 `SIGTERM`；通过 `await_event_with_timeout` 实现三级计时。
- [ ] T069 [US4] 跑 `pytest tests/integration/test_p4_* tests/unit/test_cancel_* tests/integration/test_cancel_approval_race.py -q` 全绿。

**Checkpoint**: 用户"能刹车"；宪法 Article III 的行为表面可演示。

---

## Phase 7: User Story 5 — 崩溃隔离 (Priority: P5)

**Goal**: Worker 崩溃 / 超限不得影响主循环或其他 Worker；资源上限触发 `sandbox_limit`；硬终止触发 `hard_terminated`。

**Independent Test**: `quickstart.md §5.1`；SC-005。

### Tests for US5 (RED)

- [ ] T070 [P] [US5] Write failing integration test `tests/integration/test_p5_crash_isolation.py`：并行 3 条 trace，1 条 Worker 崩溃；断言 SC-005 + 内核 pid 不变；**并显式断言 INV-4**（活下来的两条 trace 的非终态 Task 全程存在于 `kernel.runtime_tasks` 集合中；崩溃的那条 Task 从 `runtime_tasks` 中被移除并仅保留终态审计事件）。
- [ ] T071 [P] [US5] Write failing unit test `tests/unit/test_resource_monitor.py`：mock psutil；断言 500 ms 采样周期 + 超限立即转 `failed(sandbox_limit)`。
- [ ] T072 [P] [US5] Write failing integration test `tests/integration/test_budget_exceeded.py`：Worker 故意超 `wall_clock_ms` budget；断言 `failed(budget_exceeded, dim=wall)`。

### Implementation for US5 (GREEN)

- [ ] T073 [US5] Implement resource monitor in `src/orchestrator_kernel/worker_supervisor/lifecycle.py`（扩展 T068 的模块）：psutil 每 500 ms 采样 rss/cpu；超 ResourceLimits 立即强终止并落 `sandbox_limit_hit` + `task_failed(sandbox_limit)`。
- [ ] T074 [US5] Implement Windows Job Object binding in `src/orchestrator_kernel/worker_supervisor/supervisor.py`：使用 `ctypes` 调 `CreateJobObjectW` + `AssignProcessToJobObject` + `SetInformationJobObject(JobObjectExtendedLimitInformation)`；POSIX 下退化为 `resource.setrlimit` 或仅靠 psutil 软监控并记 warning。
- [ ] T075 [US5] Implement `crash-worker` stub in `src/workers_stub/crash_worker.py`：capability `crash.oom`（不断 `bytearray`）+ `crash.raise`（`raise RuntimeError`）；两种均不接入 stdio 回传。
- [ ] T076 [US5] Implement heartbeat / unhealthy 升级 in `lifecycle.py`：连续 3 次缺心跳 → `worker_unhealthy`；恢复 → `worker_recovered`；unhealthy Worker 被 dispatcher 跳过。
- [ ] T077 [US5] Implement budget wall/tool/token 监控接入 dispatcher：每次 dispatch 注入 `deadline = dispatchedAt + budget.wall_clock_ms`；tool/token 由 Worker 自觉上报（MVP 仅占位）。
- [ ] T078 [US5] 跑 `pytest tests/integration/test_p5_* tests/unit/test_resource_monitor.py tests/integration/test_budget_exceeded.py -q` 全绿。

**Checkpoint**: 五条核心 user story 完结；系统具备 P1~P5 的运行韧性。

---

## Phase 8: User Story 6 — 崩溃恢复 + 结果回推 (extension, from clarify Q4)

**Goal**: 内核崩溃后 ≤ 10 s 内扫描审计、为 in-flight Task 补 `failed(kernel_restart)`、为每条受影响 trace 主动推 ResultSummary（含重投提示）；所有 trace 终态均经来源通道回推，用户不依赖轮询。

**Independent Test**: `quickstart.md §5.2` + SC-009 / SC-010。

### Tests for US6 (RED)

- [ ] T079 [P] [US6] Write failing integration test `tests/integration/test_kernel_restart_recovery.py`：3 条 in-flight trace；`Stop-Process` 内核；重启；断言 ≤ 10 s 内三条 trace 均补写 `failed(kernel_restart)` + 对应 ResultSummary 经原通道投递成功；重启扫描期内 submit 收到 `rejected(kernel_warming_up)`。
- [ ] T080 [P] [US6] Write failing integration test `tests/integration/test_result_notification.py`：50 条 trace 正常完成；断言首次投递成功率 ≥ 99%（用注入 1% 一次性抖动 mock）；重试后 100%；失败事件落 `notification_delivery_failed`。
- [ ] T081 [P] [US6] Write failing unit test `tests/unit/test_audit_scanner.py`：给定人造 JSONL（含未终态 Task），scanner 能正确识别并补写终态事件；INV-5 / INV-6 属性测试。

### Implementation for US6 (GREEN)

- [ ] T082 [US6] Implement audit scanner in `src/orchestrator_kernel/audit/scanner.py`：`scan_and_autofail(audit_dir) -> list[AutoFailedTrace]`；顺序扫描最近 N 天，构建 `{taskId: last_state}`；对未终态集合写 `in_flight_auto_failed` + `task_failed(kernel_restart)`。
- [ ] T083 [US6] Upgrade `ResultSummary` generator in `src/orchestrator_kernel/notifier/result_summary.py` (替换 T046 的最小版)：按叶 Task 聚合 + 生成 `commandDigest`（原 text → redact → 截断到 256 字符）+ `kernel_restarted` 分支的重投提示。
- [ ] T084 [US6] Implement delivery with retry in `src/orchestrator_kernel/notifier/delivery.py`：`deliver(summary, channel)` + backoff 序列 `[0, 1, 4, 16]` s；每次失败写 `result_summary_retrying`；3 次全失败写 `notification_delivery_failed`。
- [ ] T085 [US6] Wire 内核启动序列 in `src/orchestrator_kernel/cli_main.py`：`startup()` → scanner → 批量推送 ResultSummary → 打开入口；扫描期间入口拒 `event_rejected_warming_up`。
- [ ] T086 [US6] 将正常 trace 的终态也接入 delivery 路径（US1 T046 占位替换）。
- [ ] T087 [US6] 跑 `pytest tests/integration/test_kernel_restart_recovery.py tests/integration/test_result_notification.py tests/unit/test_audit_scanner.py -q` 全绿，并更新 `validation.md` 记录 SC-009 / SC-010 实测。

**Checkpoint**: 用户无需轮询即可得知每条 trace 的终态；崩溃恢复闭环完成。

---

## Phase 9: User Story 7 — 输入护栏（rate limit + payload size）(extension, from clarify Q3/Q5)

**Goal**: FR-025/026/027 四维并发与速率限制 + FR-031 16 KB payload 上限全量落地并接入入口管线。

**Independent Test**: `pytest tests/integration/test_rate_limit.py tests/integration/test_payload_size.py`；SC-011。

### Tests for US7 (RED)

- [ ] T088 [P] [US7] Write failing integration test `tests/integration/test_rate_limit.py`：四维独立场景 + 组合场景；断言 `rejected(reason=rate_limited, dimension=…)` 审计结构并且 Task 队列长度不增。
- [ ] T089 [P] [US7] Write failing integration test `tests/integration/test_payload_size.py`：100 条 32 KB~2 MB 超大 payload；断言全部在 50 ms 内被拒（SC-011）；Task 队列不增；内核 CPU/mem 无尖峰（通过 psutil 采样）。
- [ ] T090 [P] [US7] Write failing integration test `tests/integration/test_highrisk_flood.py`：同用户在既有 HIGH_RISK `pending_approval` 期间再投递 HIGH_RISK 事件；断言按 FR-025 第二条拒绝 `rate_limited(user_highrisk_concurrent)`，不触发第二次审批消息。

### Implementation for US7 (GREEN)

- [ ] T091 [US7] Wire payload-size guard（T033）为入口管线**第一步**，先于 schema 校验；在 `cli_main.py` 的 intake pipeline 修改。
- [ ] T092 [US7] Wire rate limiter (T035) 为入口管线**schema 校验后、idempotency 前**的 gate；按 NORMAL / HIGH_RISK 分支使用不同 counter。
- [ ] T093 [US7] 在 rate-limiter 增加 `user_highrisk_concurrent` 维度，数据源为"当前 Trace 中 state ∈ {pending_approval, dispatched, running} 且 leaf.riskLevel=HIGH_RISK 的计数"；与 approval gate 共享状态（通过 `KernelState` 单例或 event subscribe）。
- [ ] T094 [US7] 跑 `pytest tests/integration/test_rate_limit.py tests/integration/test_payload_size.py tests/integration/test_highrisk_flood.py -q` 全绿；记录 SC-011 实测。

**Checkpoint**: 输入侧防御墙完成；可演示超大 payload / 审批洪水被秒拒的场景。

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: 非阻塞但必须在 `/speckit-analyze` 与合并 `main` 前完成的收尾项。

- [ ] T095 [P] Write property test `tests/unit/test_state_machine_property.py` 守护 INV-2（hypothesis 生成任意 state 序列，断言单向）。
- [ ] T096 [P] Write property test `tests/unit/test_audit_redact_property.py` 守护 INV-8（hypothesis 生成随机敏感 payload，grep 最终 JSONL 行断言无明文）。
- [ ] T097 [P] Implement HTTP entry stub in `src/orchestrator_kernel/entrypoints/http.py`（FastAPI，FR-003 预留；MVP 挂一个 `POST /submit` 直调 intake pipeline）。
- [ ] T098 [P] Implement `feishu_stub` entry in `src/orchestrator_kernel/entrypoints/feishu_stub.py`：只打印"[feishu_stub] received"，证明 channel abstraction 可扩展。
- [ ] T099 [P] Define `LLMClient` Protocol in `src/orchestrator_kernel/llm/client.py`（无实现；留一个 `NotImplementedLLMClient` 作默认占位，调用即 raise）。
- [ ] T100 [P] Write `src/orchestrator_kernel/README.md`：对齐 `quickstart.md` 的开发者 recap；列 entry points 与 subpackage 职责。
- [ ] T101 Run `quickstart.md §2~§5` 全程手动演练；将输出 + 时延 + 审计片段写入 `specs/001-orchestrator-kernel/validation.md`（首次人工验收演练证据，宪法 Article VIII 合并 `main` 的条件之一）。
- [ ] T102 跑 `pytest -q`（全套）+ `ruff check src tests` + `mypy src`；三者皆绿。
- [x] T103 更新 spec.md FR-024 措辞："5 类 schema" → "9 类 schema" + 版本号自 `1.0.0 → 1.1.0`（MINOR 扩展）；同步到 `checklists/requirements.md` 的 Resolution Log。**（已在 /speckit-analyze 后的 R2 补丁中提前完成，2026-04-21）**
- [ ] T104 在 `specs/001-orchestrator-kernel/analysis-precheck.md` 写一张 FR × Task 覆盖矩阵，供下一步 `/speckit-analyze` 消费。

**Checkpoint**: 全绿 + 手动验收 + 覆盖矩阵齐备；具备 `/speckit-analyze` 条件。

---

## Dependencies & Execution Order

### Phase 依赖

```text
Phase 1 (Setup)
   │
   ▼
Phase 2 (Foundational) ——— contracts + infra，一切 user story 的门槛
   │
   ├─▶ Phase 3 US1 (MVP) ── 独立可测
   │      │
   │      ▼
   ├─▶ Phase 4 US2  ────── 需要 US1 的 intake pipeline，但实现逻辑独立
   ├─▶ Phase 5 US3  ────── 需要 US1 的 dispatcher，逻辑上新增审批门
   ├─▶ Phase 6 US4  ────── 需要 US1（+ 可选 US3 的 pending_approval）
   ├─▶ Phase 7 US5  ────── 需要 US1 的 supervisor，扩展资源监控
   ├─▶ Phase 8 US6  ────── 依赖 audit writer (Phase 2) + 全部 user story 就位后更能端到端演练
   └─▶ Phase 9 US7  ────── 依赖 Phase 2 的 rate limiter + validators，独立于其它 US
         │
         ▼
Phase N (Polish)  ───────── 依赖上述全部完成
```

### Story 间独立性说明

- **US1** MVP 独立；完成后可停下来演示。
- **US2、US3、US5、US7** 彼此独立（不同模块接入同一 pipeline 的不同 gate / 执行层），可由不同工程师并行推进。
- **US4** 与 US3 有轻耦合：cancel 需要能中断 `pending_approval` 状态，但 `pending_approval` 状态在 Phase 2 的 state machine 就已定义，因此 US4 可独立于 US3 完成。
- **US6** 最好在 US1~US5 稳定后启动（需要真实终态多样性产生审计数据）；但 US6 的 scanner 单元测试可更早。

### Parallel opportunities

- Phase 2A 的 9 个 contract tests（T007~T015）全部 [P]。
- Phase 2B 的 9 个 pydantic 镜像（T016~T024）全部 [P]。
- Phase 2C 内的 T027 / T028 / T030 / T032 / T034 均可并行。
- 每个 user story 内的 "tests first" 三条均 [P]。
- Polish 阶段 T095~T100 全部 [P]。

### Parallel example — Phase 2A 一次性派 9 位工程师

```bash
# 9 个合约测试文件，互不干涉：
Task: "T007 entry-event contract test in tests/contract/test_entry_event.py"
Task: "T008 task contract test in tests/contract/test_task.py"
Task: "T009 budget contract test in tests/contract/test_budget.py"
Task: "T010 worker-registration contract test in tests/contract/test_worker_registration.py"
Task: "T011 worker-protocol contract test in tests/contract/test_worker_protocol.py"
Task: "T012 approval contract test in tests/contract/test_approval.py"
Task: "T013 cancel contract test in tests/contract/test_cancel.py"
Task: "T014 audit-event contract test in tests/contract/test_audit_event.py"
Task: "T015 result-summary contract test in tests/contract/test_result_summary.py"
```

---

## Implementation Strategy

### MVP First (US1 only)

1. Phase 1 Setup
2. Phase 2 Foundational（**强制**：所有 user story 依赖）
3. Phase 3 US1
4. 停下来按 `quickstart.md §2` 做一次端到端演练
5. commit `hjx`（附更新说明）+ push；**不合并 main**（宪法 Article VIII）

### Incremental delivery

- MVP → + US2（幂等）→ + US3（审批门）→ + US4（取消）→ + US5（崩溃隔离）→ + US6（恢复 + 推送）→ + US7（输入护栏）→ Polish
- 每轮演练按 `quickstart.md` 相应小节；每轮在 `hjx` 提交并推送远端。
- 全量完结 + Polish + `/speckit-analyze` 通过后，按宪法 Article VIII 合并 `main` + 打 SemVer tag（如 `v0.1.0`）。

### Parallel team strategy

- 开发者 A：Phase 2A + Phase 3 US1（主干）
- 开发者 B：Phase 2B + Phase 4 US2
- 开发者 C：Phase 2C infra（audit / state machine / validators / rate limit）
- 开发者 D（后期介入）：Phase 6 US4 + Phase 7 US5 + Phase 8 US6 + Phase 9 US7

---

## Task coverage × FR / SC matrix (partial preview, full version in analysis-precheck.md per T104)

| FR / SC | Covered by |
|---|---|
| FR-001 | T007, T016, T091 |
| FR-002 / FR-022 / FR-023 | T048~T054 |
| FR-003 | T044, T097, T098 |
| FR-004 / FR-005 | T008, T017, T028, T029, T039 |
| FR-006 / FR-019 / FR-020 / FR-021 | T014, T023, T027, T030, T031 |
| FR-007 / FR-008 / FR-009 | T010, T019, T040, T076 |
| FR-010 / FR-011 / FR-012 | T055~T061 |
| FR-013 / FR-014 / FR-015 | T062~T069 |
| FR-016 / FR-017 / FR-018 | T041, T073, T074, T077 |
| FR-024 | T007~T024, T103 |
| FR-025 / FR-026 / FR-027 | T034, T035, T088, T092, T093 |
| FR-028 / FR-029 / FR-030 | T079~T087 |
| FR-031 | T032, T033, T089, T091 |
| SC-001 | T101 (quickstart manual validation) |
| SC-002 | T036, T047 |
| SC-003 | T048 |
| SC-004 | T062, T069 |
| SC-005 | T070, T078 |
| SC-006 | T030, T031, T082 |
| SC-007 | T055 (含 approve/deny/timeout ≥5 次) |
| SC-008 | T030, T031 |
| SC-009 | T079, T087 |
| SC-010 | T080, T087 |
| SC-011 | T089, T094 |

---

## Notes

- 本 tasks.md 为 `/speckit-tasks` 阶段产出；实现阶段启动 MUST 严格走 **红 → 绿 → 重构** 节奏。
- 每个已完成任务 SHOULD 单独（或按小簇）commit 到 `hjx`，commit message 描述 "变更点 / 影响范围 / 测试结论"，对齐 Constitution Article VIII 要求。
- **禁止**在 `/speckit-analyze` 通过前合并 `main`；禁止对 `main` force-push。
- 总任务数：**104 条**（Setup 6 + Foundational 29 + US1 12 + US2 7 + US3 7 + US4 8 + US5 9 + US6 9 + US7 7 + Polish 10）。
- `[P]` 任务数：**61 条**（高并行度，得益于契约先行与 user story 独立性）。
