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

- [x] T001 Create `pyproject.toml` at repo root with `[project]` metadata, `requires-python = ">=3.11"`, and runtime deps (`pydantic>=2.7`, `anyio>=4`, `structlog>=24`, `typer>=0.12`, `psutil>=5.9`, `jsonschema>=4.21`, `ulid-py>=1.1`) + dev deps (`pytest>=8`, `pytest-asyncio>=0.23`, `hypothesis>=6`, `ruff>=0.4`, `mypy>=1.10`). Register console script `orchestrator-kernel = "orchestrator_kernel.cli_main:app"`. *(dry-run 2026-04-21: hatchling 后端；dev deps 迁移到 PEP 735 `[dependency-groups]`；加 `pydantic-settings>=2.2`；落 `cli_main.py` typer 占位。)*
- [x] T002 Scaffold `src/orchestrator_kernel/` package tree per `plan.md` §Project Structure. Create `__init__.py` in each subpackage (`contracts/`, `entrypoints/`, `kernel/`, `worker_supervisor/`, `audit/`, `notifier/`, `llm/`). Create `src/workers_stub/` with `__init__.py`. *(dry-run 2026-04-21: 9 个 `__init__.py` + `cli_main.py` 占位。)*
- [x] T003 [P] Scaffold `tests/` tree: `tests/contract/`, `tests/integration/`, `tests/unit/`, each with `__init__.py` and a root `conftest.py` that yields a `tmp_audit_dir` fixture (uses `tmp_path`). *(dry-run 2026-04-21 完成。)*
- [x] T004 [P] Configure quality tooling in `pyproject.toml`: `[tool.ruff]` (select E,F,I,W,UP,B; line-length 100); `[tool.mypy]` (strict = true; files = src); `[tool.pytest.ini_options]` (asyncio_mode = "auto"; addopts = "-ra"); `[tool.hypothesis]` (deadline = 2000). *(dry-run 2026-04-21 完成，额外追加 `pydantic.mypy` plugin + `pythonpath=["src"]` 以便测试导入。)*
- [x] T005 [P] Extend `.gitignore` with `var/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `dist/`, `*.egg-info/`. *(dry-run 2026-04-21 完成；`*.egg-info/` 原已存在，新增 `var/` / 工具缓存 / `dist/` / `build/` / `.hypothesis/`。)*
- [x] T006 Run `uv sync` and confirm `pytest -q` executes (empty suite passes). Record command in `specs/001-orchestrator-kernel/validation.md` as first evidence line. *(dry-run 2026-04-21: `uv sync` 安装 39 包；`uv run pytest -q` → **3 passed in 0.14s**（scaffold smoke）；validation.md Evidence #1 已落盘。偏离：为避免 pytest exit-code 5（no-tests-collected），补了 `tests/test_scaffold.py` 3 条烟雾测试，不影响后续 RED 测试任务。)*

**Checkpoint**: `uv run pytest -q` 绿灯（实际 3 条 scaffold smoke，见 T006 偏离说明）。

---

## Phase 1.5: Setup Follow-up (post dry-run self-review, 2026-04-21)

**Purpose**: dry-run T001–T006 完成后做的"自审"挖出来的 5 条 MEDIUM finding。
本阶段全部 [P]，**不阻塞** Phase 2 启动；可以与 T007–T015 RED 任务并行落地，
也可以挪到 Polish 阶段处理。每条都对应 review 报告中的编号（H1/H2/M1–M5）。

- [x] T006a [P] **(H1)** Fix ruff E501 baseline reds：拆 `src/orchestrator_kernel/kernel/__init__.py` 单行 docstring → 多行；`src/orchestrator_kernel/cli_main.py` 的 `help=` 提取为常量 `_HELP`。验收：`uv run ruff check src tests` 0 errors。*(已完成 2026-04-21 fix-and-commit 批次)*
- [x] T006b [P] **(H2)** 去除 `pyproject.toml` 中 `[project.optional-dependencies].dev` 与 `[dependency-groups].dev` 的双份重复；保留 PEP 735 `[dependency-groups]` 作唯一 dev deps 来源；上方加注释说明 pip 用户的 fallback 命令。验收：grep `optional-dependencies` 返回 0 行。*(已完成 2026-04-21 fix-and-commit 批次)*
- [x] T006c [P] **(M5)** 在 `tests/test_scaffold.py` 文件顶部加 `⚠️ TRANSITIONAL FILE` 注释，说明它是 Phase 1 闸门，T007 落盘后 SHOULD remove；列出 4 条 trivially-asserted invariants 与 lifecycle。验收：人工 review 通过。*(已完成 2026-04-21 fix-and-commit 批次)*
- [x] T006d [P] **(M1)** 重新评估 `src/workers_stub/` 的 wheel 打包策略：决策 = 选项 A（保留 `src/workers_stub/` 在 pythonpath 下，从 `[tool.hatch.build.targets.wheel].packages` 中剔除）；research.md 新增 **R-11 workers_stub 的包 layout 与 wheel 打包策略** 详述 rationale / alternatives / verification。验收：`uv build` 后 wheel 内不含 `workers_stub/` 目录。*(已完成 2026-04-21 finish-1.5 批次)*
- [x] T006e [P] **(M2)** pytest 包结构合规性回查：**删除** `tests/{contract,integration,unit}/__init__.py` 与 `tests/__init__.py` 共 4 个文件（pytest 官方推荐 namespace 包）；`tests/conftest.py` 顶部新增 layout note 说明"tests/ 故意不含 __init__.py，靠 rootdir + testpaths 发现"；回归 `uv run pytest --collect-only` → 3 tests collected 保持不变。验收：pytest 仍绿。*(已完成 2026-04-21 finish-1.5 批次)*
- [x] T006f [P] **(M3)** 新建 `.gitattributes`：`* text=auto eol=lf`、`*.py/*.toml/*.md/*.json/*.yml/*.yaml/*.sh/uv.lock text eol=lf`、`*.ps1/*.psm1/*.cmd/*.bat text eol=crlf`、二进制文件标 `binary`、`uv.lock` 标 `linguist-generated=true`；首次 stage 后跑 `git add --renormalize .`（无额外 diff，表明现存文件将随下次写入自动规范化）。验收：`.gitattributes` 落盘。*(已完成 2026-04-21 finish-1.5 批次)*
- [x] T006g [P] **(M4)** 重写 repo 根 `README.md`：新增 "Quickstart for developers" 5 步（装 uv / `uv sync` / 4 条质量基线命令 / TDD 循环入口 / 验证证据文件指路）；补 "工件位置" 的当前功能详细清单（含 9 份 contracts + 3 份 validation artifact）；补 "分支策略（宪法 Article VIII）" 一节。验收：人工 review。*(已完成 2026-04-21 finish-1.5 批次)*

**Checkpoint**: ✅ Phase 1.5 七条全部完成（H1/H2/M5 已在上一 commit、M1–M4 本 commit）。Phase 1 Setup **零债务收尾**；可以启动 Phase 2 Foundational（T007 契约测试 RED）。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 契约层（9 份 pydantic 镜像 + 对应 JSON Schema 双向校验）、审计基础设施、状态机、配置、输入护栏（payload size + 限流）。**任一 user story 均依赖本阶段完成。**

**⚠️ CRITICAL**: User story 工作 MUST NOT 在本 Phase 完成前启动。

### 2A · Contract tests (RED — all [P], one file each)

- [x] T007 [P] Write failing contract test in `tests/contract/test_entry_event.py`: 正例合规；反例覆盖缺字段、`text > 16_384 bytes`、非法 `sourceChannel`、`eventId` 长度越界。引用 `specs/001-orchestrator-kernel/contracts/entry-event.schema.json`。
- [x] T008 [P] Write failing contract test in `tests/contract/test_task.py`: root_intent vs leaf_action 分支；leaf 缺 `capability`/`riskLevel`/`budget` 应拒；state/outcome 交叉一致性；`resultHash` pattern。
- [x] T009 [P] Write failing contract test in `tests/contract/test_budget.py`: 三维边界（min=1、硬顶 1_800_000 / 200 / 500_000）；越界应拒。
- [x] T010 [P] Write failing contract test in `tests/contract/test_worker_registration.py`: capability name pattern、riskLevel 枚举、`resourceLimits` 范围；capability 超硬顶 budget 注册应拒。
- [x] T011 [P] Write failing contract test in `tests/contract/test_worker_protocol.py`: 覆盖全部 7 种帧（register/dispatch/started/result/heartbeat/abort/shutdown）的正反例；discriminator `kind` 错误应拒。
- [x] T012 [P] Write failing contract test in `tests/contract/test_approval.py`: request 与 response 两支；userId 长度、decision 枚举、expiresAt 必填。
- [x] T013 [P] Write failing contract test in `tests/contract/test_cancel.py`: 必需字段、userId 越界拒绝。
- [x] T014 [P] Write failing contract test in `tests/contract/test_audit_event.py`: 全部 36 种 `eventType` 枚举断言；`actor` pattern；`input_hash`/`output_hash` pattern；nullable `traceId` 路径。
- [x] T015 [P] Write failing contract test in `tests/contract/test_result_summary.py`: `traceOutcome` 枚举、`leafResults` items schema、`deliveryAttempt` ≤ 3；`kernel_restarted` 时 `message` 必须含重投提示。

### 2B · Pydantic mirrors (GREEN — all [P], one file each)

- [x] T016 [P] Implement `EntryEvent` in `src/orchestrator_kernel/contracts/entry_event.py` (pydantic v2；`model_config = ConfigDict(extra="forbid")`；`text` 长度校验器按 UTF-8 字节数计算)。
- [x] T017 [P] Implement `Task` / `TaskState` / `TaskOutcome` / `FailureReason` enums + model in `src/orchestrator_kernel/contracts/task.py`；嵌套 leaf-specific 校验（pydantic `model_validator(mode="after")`）。
- [x] T018 [P] Implement `Budget` + `DEFAULT_BUDGET` + `SYSTEM_HARD_CAP` constants in `src/orchestrator_kernel/contracts/budget.py`.
- [x] T019 [P] Implement `Capability`, `ResourceLimits`, `WorkerRegistration` in `src/orchestrator_kernel/contracts/worker.py`；在 model_validator 中断言 `capability.budget` 不越硬顶。
- [x] T020 [P] Implement discriminated union `WorkerFrame` (Register/Dispatch/Started/Result/Heartbeat/Abort/Shutdown) in `src/orchestrator_kernel/contracts/worker_protocol.py` 使用 pydantic `Discriminator("kind")`.
- [x] T021 [P] Implement `ApprovalRequest`/`ApprovalResponse` + `ApprovalMessage` union in `src/orchestrator_kernel/contracts/approval.py`.
- [x] T022 [P] Implement `CancelMessage` in `src/orchestrator_kernel/contracts/cancel.py`.
- [x] T023 [P] Implement `AuditEvent` + `EventType` Literal union in `src/orchestrator_kernel/contracts/audit.py`.
- [x] T024 [P] Implement `ResultSummary` + `LeafResult` in `src/orchestrator_kernel/contracts/result_summary.py`.
- [x] T025 Run `pytest tests/contract/ -q`；全部变绿；round-trip（pydantic → `model_json_schema()` → 与 `contracts/*.schema.json` 断言一致）作为 contract test 的内嵌子用例。

### 2C · Shared infra

- [x] T026 Implement `src/orchestrator_kernel/config.py`: 使用 pydantic-settings 加载 `var/kernel.toml`；暴露 `system_hard_cap`、`approval_timeout_default_ms=600_000`、`payload_max_bytes_default=16_384`、`rate_limit_defaults`；启动时对任何部署配置越界值 raise。
- [x] T027 [P] Implement hashing + redact helpers in `src/orchestrator_kernel/audit/hasher.py` (SHA-256 截前 16 bytes hex) 与 `src/orchestrator_kernel/audit/redact.py` (structlog processor；按字段名 allowlist + 值长度阈值双策略)。
- [x] T028 [P] Write failing unit test `tests/unit/test_state_machine.py`: Task.state 单向转换表；`succeeded/failed/cancelled/denied/denied_by_timeout` 为终态（任何出边应 raise）；HIGH_RISK 必经 `pending_approval`。
- [x] T029 Implement Task state machine in `src/orchestrator_kernel/kernel/state_machine.py`：一个 `transition(task, to_state, *, reason=None) -> Task` 纯函数，内含 INV-2 / INV-3 断言。
- [x] T030 [P] Write failing unit test `tests/unit/test_audit_writer.py`: JSONL append; UTC 日切 rotation；`disk_write_failed` 事件在磁盘不可写时落盘并切入"拒绝新入口"（通过依赖注入模拟磁盘故障）。
- [x] T031 Implement audit writer in `src/orchestrator_kernel/audit/writer.py`：结合 structlog JSONL processor、`O_APPEND` 写入、每天 UTC 0 点 rotate；disk-full handling 通过 `IOError` 捕获 + 切状态 + 自审计；暴露 `AuditWriter.write(AuditEvent) -> None` 与 `AuditWriter.healthy: bool`。
- [x] T032 [P] Write failing unit test `tests/unit/test_payload_size.py`: 正好 16_384 bytes 通过；16_385 bytes 拒；`rejected(reason=payload_too_large, limit=16KB, actual=<bytes>)` 审计事件结构正确。
- [x] T033 Implement payload-size guard in `src/orchestrator_kernel/kernel/validators.py`：`assert_payload_size(event)` 先于 schema 校验运行（FR-031）。
- [x] T034 [P] Write failing unit test `tests/unit/test_rate_limit.py`: 覆盖 4 个维度（`global_rps=50`、`user_rpm=120`、`user_concurrent=10`、`user_highrisk_concurrent=1`）；`rejected(reason=rate_limited, dimension=…)` 事件结构。
- [x] T035 Implement rate limiter in `src/orchestrator_kernel/kernel/rate_limit.py`：token bucket (全局 rps) + sliding window (per-user rpm) + counter (per-user concurrent)；使用 `anyio.Lock` 保护。

**Checkpoint**: ✅ Phase 2 **全部 29 条任务（T007~T035）完成 2026-04-21**。
- 2A-RED (commit 26a0732)：9 份 failing 契约测试 + tests/contract/_common.py helper。
- 2B-GREEN (commit a7e3705)：9 份 pydantic 镜像 + T025 round-trip。tests/contract/__init__.py 恢复（相对导入 helper 需真实 package；tests/unit, tests/integration 仍 namespace）。
- 2C-infra (commit 本批)：T026 config.py（pydantic-settings + TOML optional）；T027 hasher.py + redact.py；T028~T029 state_machine.py（INV-2 终态 / INV-3 HIGH_RISK 强制走 pending_approval）；T030~T031 AuditWriter（JSONL append + UTC 日切 + 磁盘失败降级 healthy=False）；T032~T033 validators.py（payload ≤ 16 KB UTF-8 bytes，先于 schema 跑）；T034~T035 RateLimiter（token bucket + sliding window + 4 维 counter）。
- 验证：`uv run pytest -q` **406 passed**（契约 345 + 契约 round-trip 3 + state_machine 29 + audit_writer 6 + payload_size 8 + rate_limit 12 + scaffold 3）；`ruff` 全绿；`mypy --strict` 全绿，26 source files。
- 契约与基础设施就位，user stories (Phase 3~9) 可并行启动。

---

## Phase 3: User Story 1 — 基本分派闭环 (Priority: P1) 🎯 MVP

**Goal**: Alpha 用户通过 CLI 提交一条 `echo hello`，内核创建 Task 树、分派给 `echo-worker`、写审计、回传结果。

**Independent Test**: 按 `quickstart.md §2` 演练；审计可从 `event_received` 追到 `result_summary_delivered`；p95 ≤ 3 s (SC-002)。

### Tests for US1 (RED)

- [x] T036 [P] [US1] Write failing integration test `tests/integration/test_p1_basic_loop.py` — 已完成 3A-RED 批次。4 条 test：端到端 all_succeeded / 审计链完整 / SC-002 p95 ≤ 3s / trace+event IDs。RED 信号：`assemble_kernel` ImportError（T045 兑现）。
- [x] T037 [P] [US1] Write failing integration test `tests/integration/test_p1_no_worker.py` — 已完成 3A-RED 批次。3 条 test：desktop.click 无 worker / task_failed 审计 + failureReason=no_capable_worker / 零 worker 注册。RED 信号同 T036。
- [x] T038 [P] [US1] Write failing contract-level integration test `tests/integration/test_worker_stdio_roundtrip.py` — 已完成 3A-RED 批次。3 条 test：register 帧字节干净 / dispatch-started-result 时序 / 非法 JSON 不崩父进程。RED 信号：`src/workers_stub/echo_worker.py` 不存在（T043 兑现）。
- **3A-RED 基础设施**：新增 `tests/integration/__init__.py`（声明 package）+ `tests/integration/_harness.py`（KernelHarnessProtocol / TraceResult / WorkerSpec dataclass）+ `tests/integration/conftest.py`（`kernel_harness` async fixture + `echo_worker_script` + `audit_events_factory`）；`pyproject.toml` `[tool.pytest.ini_options].markers` 注册 `integration` 标签；`tests/conftest.py` Layout note 同步说明 integration 也是真实 package。

### Implementation for US1 (GREEN)

- [x] T039 [US1] Implement Task tree builder in `src/orchestrator_kernel/kernel/task_tree.py`：`build_from_event(event, capability_plan) -> Task tree`；MVP 支持根 + 单叶。*(3B-GREEN 批次 1 / 2026-04-21：`LeafPlan` DTO + `TaskTree(root, leaves)` frozen dataclass；ULID-26 taskId；multi-leaf-ready 但 MVP 只走 single leaf；trace_id 由调用方注入（避免与幂等层重复造）。)*
- [x] T040 [US1] Implement capability dispatcher in `src/orchestrator_kernel/kernel/dispatcher.py`：按 `(capability, healthy)` 匹配 Worker；无匹配落 `failed(no_capable_worker)`。*(3B-GREEN 批次 1 / 2026-04-21：`WorkerHandle` / `Dispatcher` / `NoCapableWorkerError`；capability 索引 O(1)；`assign()` 校验 `kind==leaf_action` 与 `capability!=None`；unhealthy 候选通过异常携带供审计引用。)*
- [x] T041 [US1] Implement subprocess worker supervisor in `src/orchestrator_kernel/worker_supervisor/supervisor.py`：`spawn(worker_script_path, *, creationflags=CREATE_NEW_PROCESS_GROUP)`；绑定 Windows Job Object（占位 API，T074 补全真实限制）。*(3B-GREEN 批次 1 / 2026-04-21：`SupervisedWorker` / `WorkerSupervisor` / `WorkerSpawnError`；Win32 走 `CREATE_NEW_PROCESS_GROUP`，POSIX 走 `start_new_session=True`；`_bind_job_object(pid)` 显式 no-op + TODO T074；`terminate/kill/shutdown` 吞 `ProcessLookupError` 保证 teardown 幂等。本批次 cli_main 尚未接入，模块实现完备但暂无真实 subprocess 调用。)*
- [x] T042 [US1] Implement stdio JSON-lines protocol reader/writer in `src/orchestrator_kernel/worker_supervisor/protocol.py`：异步读 stdout、按行 JSON parse + pydantic `WorkerFrame` 校验；异步写入 dispatch/abort/shutdown 帧。*(3B-GREEN 批次 1 / 2026-04-21：`encode_frame/decode_frame` 纯字节 codec + `read_frames/write_frame` async helpers（typed against `asyncio.StreamReader/Writer`）；malformed frame -> `ProtocolFrameError(raw=...)` 夹带 ≤256 byte 预览；`read_frames()` 遇异常 `continue` 以兑现 T038 test 3 的"父进程存活"契约；编码侧守 `\n` in body 防破包。)*
- [x] T043 [US1] Implement `echo-worker` stub in `src/workers_stub/echo_worker.py`：一个独立 `python -m` 可执行脚本；启动即发 `register` 帧（capability `echo.say` NORMAL）；收到 `dispatch` 后 50 ms 内回 `started` 再回 `result(succeeded, output.text=payload.text)`。*(3B-GREEN 批次 2 / 2026-04-21：同步 stdin 读 + stdout 二进制写 + LF 帧；register 用真正的 `RegisterFrame` 模型序列化（`model_dump_json(exclude_none=True)`）保证跟契约测试同 codec；malformed JSON 走 continue 不崩父进程；`shutdown` 帧干净退 0。)*
- [x] T044 [US1] Implement CLI `submit` command in `src/orchestrator_kernel/entrypoints/cli.py` 使用 typer：参数 `--text`（必需）、`--event-id`（可选自动生成）、`--user-id`（可选从 env 取）、`--source-channel=cli`。*(3B-GREEN 批次 2 / 2026-04-21：`Annotated[...]`+typer.Option 规避 B008；`--user-id` 默认从 `ORCHESTRATOR_USER` env 取，最终 fallback 到 `cli-user`；`--worker` 可重复指定，不传时自动 spawn 本仓库 `echo_worker.py`；sourceChannel 固定 `"cli"`（harness.submit 层写死）。)*
- [x] T045 [US1] Wire top-level async event loop in `src/orchestrator_kernel/cli_main.py`：装配 config → audit writer → validators → rate limiter → idempotency (stub) → task tree → dispatcher → supervisor → notifier；提供 `app = typer.Typer()` 供 entry_point。*(3B-GREEN 批次 2 / 2026-04-21：`assemble_kernel(audit_dir)` 异步工厂返回 `KernelHarness`；`KernelHarness.register_worker/submit/shutdown` 实现 integration 测试的 KernelHarnessProtocol；单事件 pipeline `payload-size → EntryEvent → plan stub → task_tree → dispatcher.assign → DispatchFrame/Started/Result → state_machine.transition → ResultSummary → print_to_cli`；audit 链 8 步齐备（event_received → trace_created → task_created → task_dispatched → task_started → task_succeeded → result_summary_prepared → result_summary_delivered），no_capable_worker 分支写 `task_failed(failureReason=no_capable_worker)` 后仍发 ResultSummary；rate limiter / idempotency 仍 stub 占位，T051/T092 再接。)*
- [x] T046 [US1] Implement minimal `ResultSummary` 生成 + CLI 前台打印 in `src/orchestrator_kernel/notifier/result_summary.py`（交付完整版留到 US6 T075）。*(3B-GREEN 批次 2 / 2026-04-21：`build_command_digest`（≤256 char trim with ellipsis）+ `compute_trace_outcome`（6-路 roll-up: kernel_restarted 预留 / cancelled / denied / all_succeeded / all_failed / partial_failed）+ `build_result_summary`（`leaf_outputs` map 通过 ResultFrame.output 回流给 message 字段）+ `print_to_cli`（stdout 单行 JSON，契约同 schema）。)*
- [x] T047 [US1] 跑 `pytest tests/integration/test_p1_* tests/integration/test_worker_stdio_roundtrip.py -q` 全绿；在 `validation.md` 记录端到端 p95 实测值。*(3B-GREEN 批次 2 / 2026-04-21：`pytest tests/integration -q` → **10 passed in 1.69s**（test_p1_basic_loop 4 + test_p1_no_worker 3 + test_worker_stdio_roundtrip 3）；`pytest -q` 全量 → **416 passed in 2.13s**；SC-002 p95 微基准 30 iter warm path = **0.0019 s**（远低于 3 s 预算），见 validation.md Evidence #5。)*

**Checkpoint**: ✅ Phase 3 US1 MVP 完整闭环 **2026-04-21 完成**（3A-RED T036~T038 + 3B-GREEN 批次 1 T039~T042 + 3B-GREEN 批次 2 T043~T047）；`orchestrator-kernel submit --text "echo hello"` 真机 smoke 通过，输出合同级 ResultSummary JSON。全套 **416 tests passed**；`ruff` / `mypy --strict`（33 source files）全绿。可在此单独 demo，准备启动 Phase 4 (US2 幂等)。

---

## Phase 4: User Story 2 — 幂等性保证 (Priority: P2)

**Goal**: 5 次同 eventId 投递 → 1 棵 Task 树 + 4 条 `idempotent_replay` 审计。

**Independent Test**: 按 `quickstart.md §2.4`；hypothesis state-machine 测试验证 INV-1。

### Tests for US2 (RED)

- [x] T048 [P] [US2] Write failing integration test `tests/integration/test_p2_idempotency.py`：覆盖 P2 的 3 个 Acceptance Scenarios（已完成终态、running 中、已失败三支）+ 跨用户同 eventId 独立性边界。
- [x] T049 [P] [US2] Write failing hypothesis test `tests/integration/test_p2_idempotency_property.py`：`RuleBasedStateMachine`，rules = {submit, duplicate_submit, mark_terminal}；invariants = INV-1 + `replay_count == total_lookups - unique_keys`。
- [x] T050 [P] [US2] Write failing unit test `tests/unit/test_idempotency_cache.py`：`lookup_or_register((userId, eventId)) -> (traceId, is_replay, snapshot)`；50 路 `asyncio.gather` 压测并发只允许 1 个 `is_replay=False`。

### Implementation for US2 (GREEN)

- [x] T051 [US2] Implement idempotency cache in `src/orchestrator_kernel/kernel/idempotency.py`：内存 dict + `threading.Lock`；`lookup_or_register` 返回 `(trace_id, is_replay, CachedTrace|None)`；`mark_terminal` 记录终态快照；`restore_entry` 挂钩留给 R-03 审计扫描重建。
- [x] T052 [US2] Wire idempotency check into `cli_main.py` 事件接入管线（在 payload-size / EntryEvent 校验之后、trace_created 之前）；替换 Phase 3 的 `_new_id()` 一次性 traceId stub，终态时调用 `mark_terminal` 存快照；`replay` 路径直接合成 `TraceResult` 不再落 `task_*` 审计。
- [x] T053 [US2] Audit writer `idempotent_replay` 字段在 `event_received` + `idempotent_replay` 两类事件透传（T031 AuditEvent schema 已支持）。
- [x] T054 [US2] `pytest tests/integration/test_p2_* tests/unit/test_idempotency_cache.py -q` 全绿（13/13）；整体套件仍 429 绿。

**Checkpoint**: US1 + US2 可独立 demo（含"重复投递不再副作用"的演示）。

---

## Phase 5: User Story 3 — HIGH_RISK 审批门 (Priority: P3)

**Goal**: `file.delete` 等 HIGH_RISK capability 进 `pending_approval`；approve / deny / timeout 三分支完整。

**Independent Test**: `quickstart.md §3`。

### Tests for US3 (RED)

- [x] T055 [P] [US3] Write failing integration test `tests/integration/test_p3_approval_gate.py`：覆盖 spec.md P3 的 4 个 Acceptance Scenarios（pending_approval、approve、deny、timeout）+ impersonation 边界。
- [x] T056 [P] [US3] Write failing unit test `tests/unit/test_approval_gate.py`：state guard（HIGH_RISK only / pending only）+ `handle_response` 五分支 + `sweep_expired` 到期计时 + `is_pending` 语义，12 条，使用可注入 clock 完全决定性。

### Implementation for US3 (GREEN)

- [x] T057 [US3] Implement approval gate in `src/orchestrator_kernel/kernel/approval_gate.py`：纯数据层 `ApprovalGate`（`register` / `handle_response` / `sweep_expired` / `is_pending` / `next_deadline`）；`ApprovalDecision` 枚举五分支；impersonation 不消费 pending slot；ApprovalStaleError 守双响应。
- [x] T058 [US3] Implement `danger-worker` stub in `src/workers_stub/danger_worker.py`：capability `file.delete` HIGH_RISK；收到 dispatch 后 `output.text = "would-delete <path>"`（不落盘）。
- [x] T059 [US3] CLI `approve` / `deny` 命令在 `entrypoints/cli.py` 挂壳（跨进程通信需 Phase 7 daemon mode，MVP 先友好退 2 并指向 `--auto-approve` / `--auto-deny`）；同进程 replay 通道由 `KernelHarness.submit_approval_response` 暴露，集成测试已消费。
- [x] T060 [US3] `--approval-timeout-ms` 在 `submit` 命令就位（默认 600_000 ms=10 min，FR-011），经 `assemble_kernel(approval_timeout_ms=...)` 传到 `ApprovalGate.default_window_ms`；`--auto-approve` / `--auto-deny` 提供单进程端到端演练通道。
- [x] T061 [US3] `pytest tests/integration/test_p3_* tests/unit/test_approval_gate.py -q` 17/17 绿；整套 446 绿。CLI smoke 三分支均端到端验证（auto-approve → all_succeeded "would-delete foo.txt"；auto-deny → denied/user_rejected；400ms 超时 → denied_by_timeout/approval_timeout）。

**Checkpoint**: US1~US3 均独立可用；"敢不敢对外放"的门槛到位。

---

## Phase 6: User Story 4 — 一键取消 (Priority: P4)

**Goal**: `cancel <traceId>` ≤ 5 s 内令所有 running / dispatched / pending_approval Task 进入 cancelled；软中止 3 s 未响应升级硬终止。

**Independent Test**: `quickstart.md §4`；SC-004 数值达标。

### Tests for US4 (RED)

- [x] T062 [P] [US4] Write failing integration test `tests/integration/test_p4_cancel.py` — 4 用例覆盖 P4 三个 Acceptance Scenarios + "重复 cancel" Edge Case；RED 阶段 `ModuleNotFoundError: orchestrator_kernel.kernel.cancel`。
- [x] T063 [P] [US4] Write failing unit test `tests/unit/test_cancel_signal_escalation.py` — 7 用例（obedient soft-exit / stubborn terminate / fully-stubborn kill / already-terminal no-op / 阶段 IntEnum 顺序 / `TerminationResult` 形状 / 非 async `send_soft_signal` 拒绝）；使用本地 `_FakeProcess` 替代真实 subprocess。
- [x] T064 [P] [US4] Write failing integration test `tests/integration/test_cancel_approval_race.py` — 2 用例覆盖 spec Edge Case "approval 与 cancel 竞态"：cancel-arrives-last-wins 与 approve-先入终态后-cancel 返回 `already_terminal/not_found`。

### Implementation for US4 (GREEN)

- [x] T065 [US4] Implement cancel orchestrator in `src/orchestrator_kernel/kernel/cancel.py` — 纯内存 `CancelManager`（register_trace / register_dispatch / unregister_task / mark_terminal / release / cancel_event / is_cancelled / request_cancel）+ `CancelStatus` enum（accepted / not_found / already_cancelled / already_terminal / impersonation_rejected）+ 不可变 `CancelOutcome` 数据类；无副作用，审计由 harness 统一写。
- [x] T066 [US4] Implement `sleep-worker` stub in `src/workers_stub/sleep_worker.py` — capability `sleep.wait` NORMAL，`payload.seconds` 驱动睡眠；后台 stdin reader 监听 `abort` 帧，主线程以 50 ms 粒度轮询 abort/shutdown；`SLEEP_WORKER_IGNORE_ABORT=1` 触发"装死 worker"（只有 `kill()` 生效）。
- [x] T067 [US4] Add CLI `cancel <traceId>` 子命令 — MVP 范围暂 stub（同 approve/deny 模式），输出 Phase 7 daemon 限制说明后 exit=2；`orchestrator-kernel status` 文案同步升级到 "Phase 6 US4 live"。
- [x] T068 [US4] Implement signal escalation in `src/orchestrator_kernel/worker_supervisor/lifecycle.py` — 纯异步 `soft_abort_with_escalation(process, send_soft_signal, soft_timeout_s=3.0, hard_timeout_s=1.0) -> TerminationResult`；`EscalationStage.{already_terminal,soft,terminate,kill}` IntEnum 严格递增；依赖 `ProcessLike` Protocol（read-only `returncode` property）使单元测试可注入 `_FakeProcess`；`send_soft_signal` 非 `async def` 主动抛 `TypeError` 防止悄悄跳过软阶段。
- [x] T069 [US4] `pytest tests/integration/test_p4_cancel.py tests/integration/test_cancel_approval_race.py tests/unit/test_cancel_signal_escalation.py -q` 全 13/13 绿；全量 `pytest -q` 459 通过（446 + 13 Phase 6 新增）。`ruff check src tests` 干净；`mypy src` 39 文件无错。

**Checkpoint**: 用户"能刹车"；宪法 Article III 的行为表面可演示。

---

## Phase 7: User Story 5 — 崩溃隔离 (Priority: P5)

**Goal**: Worker 崩溃 / 超限不得影响主循环或其他 Worker；资源上限触发 `sandbox_limit`；硬终止触发 `hard_terminated`。

**Independent Test**: `quickstart.md §5.1`；SC-005。

### Tests for US5 (RED)

- [x] T070 [P] [US5] Write failing integration test `tests/integration/test_p5_crash_isolation.py`：并行 3 条 trace，1 条 Worker 崩溃；断言 SC-005 + 内核 pid 不变；**并显式断言 INV-4**（所有 `task_dispatched` 有对应终态事件；崩溃那条仅剩 `task_failed(worker_crashed)`；所有 CancelManager 会话在 submit 返回后清零）。GREEN in Phase 7；通过 `pytest tests/integration/test_p5_crash_isolation.py -q`。
- [x] T071 [P] [US5] Write failing unit test `tests/unit/test_resource_monitor.py`：mock `psutil.Process`；断言 `check_limits` 超 memory_mb 返回 `Violation`；`watch_worker` 按 `interval_s` 采样直至违规；进程消失时静默退出。GREEN in Phase 7。
- [x] T072 [P] [US5] Write failing integration test `tests/integration/test_budget_exceeded.py`：budget-worker 注册 `wall_clock_ms=300` 的 `budget.burn`，dispatch 内睡 10 s 且无视 abort；断言 `task_failed(failureReason=budget_exceeded, failureDim=wall)` 且端到端 ≤ 5 s（FR-013）。GREEN in Phase 7。

### Implementation for US5 (GREEN)

- [x] T073 [US5] Implement resource monitor in `src/orchestrator_kernel/worker_supervisor/lifecycle.py`（扩展 T068 的模块）：新增 `ResourceLimitsSnapshot` + `Violation` 数据类；`check_limits(pid, limits)` 使用 `psutil.Process` 采样 rss/cpu，捕 `NoSuchProcess/AccessDenied` 视为无违规；`watch_worker` 按 `interval_s` 循环采样直到违规或进程退出。注意：MVP 未接入 Harness 实时监控闭环（真实强终止路径由 T074 Job Object 接管，见 **MVP 缺口 A**），仅提供纯函数 + 协程给后续阶段复用；单元测试已证契约。
- [x] T074 [US5] **Phase N.2 — MVP 缺口 A 已补齐；Phase N.3 / Evidence #14 追加真实内核级验证.** `worker_supervisor/supervisor.py` 实现：(1) Windows 下用 `ctypes.windll.kernel32` 调用 `CreateJobObjectW` → `SetInformationJobObject(JobObjectExtendedLimitInformation)`（`JOB_OBJECT_LIMIT_PROCESS_MEMORY` | `KILL_ON_JOB_CLOSE` | `DIE_ON_UNHANDLED_EXCEPTION`）→ `AssignProcessToJobObject`，handle 存入 `SupervisedWorker.metadata` 并在 `shutdown` 里 `CloseHandle`；(2) POSIX 下 `preexec_fn` 调 `resource.setrlimit(RLIMIT_AS, memory_mb * MiB)`。Kernel 侧接线：`KernelHarness.__init__` 新增 `resource_monitor_factory`，`register_worker` 为每个 Worker 起 `watch_worker` 协程，`Violation` 触发 `_on_sandbox_violation`（写 `sandbox_limit_hit` 审计 + `channel.frame_queue` 投错误信封唤醒 in-flight dispatch + `_tear_down_tainted_worker` 杀进程）；`_execute_leaf` 错误分支以 `_sandboxed_workers` 标志把 channel EOF 映射为 `failureReason="sandbox_limit"`。**Evidence #14 真实 OOM 验证额外补齐三个隐性 bug**：(a) ctypes 默认 `restype=c_int` 在 x64 把 HANDLE 截断到 32 bit → `_configure_kernel32_signatures` 显式固定 `HANDLE=c_void_p` 和其余 argtypes；(b) VSCode/WT 父 Job Object 让嵌套 job 的 `ProcessMemoryLimit` 静默失效 → spawn 默认附加 `CREATE_BREAKAWAY_FROM_JOB` + OSError fallback；(c) bind 必须发生在 asyncio 首次 `readline` 之前 → **两阶段绑定**：spawn 时预绑 `_DEFAULT_SPAWN_MEMORY_CEILING_MB=1024` 的宽松 Job Object，register 到达后 `bind_sandbox` 改走 `_update_job_memory_cap` 在既有 job 上 `SetInformationJobObject` 收紧到声明的 `memory_mb`，`cli_main.register_worker` 相应调用。新增单元 `tests/unit/test_supervisor_sandbox.py`（ctypes mock，3 条 Windows-only）+ `tests/unit/test_sandbox_wiring.py`（fake monitor factory 2 条）+ `tests/integration/test_job_object_oom.py`（`oom_blast_worker` + `ctypes.memset` 强制 commit 页面的真实 OOM，1 条，stderr 三层判据证明 Windows 内核 `TerminateProcess`；POSIX 自动 skip）；全量回归 481 passed / 1 skipped；`scripts/smoke-phase-n.ps1` 扩展为 6 阶段（+Phase N.3）。
- [x] T075 [US5] Implement `crash-worker` stub in `src/workers_stub/crash_worker.py`：`crash.raise` 在 dispatch 时 emit started 后 `raise RuntimeError` 使进程崩溃；`crash.oom` 循环分配 16×16 MiB bytearray 作为受控 OOM 触发器（自带 256 MiB 上限防止误伤 CI 主机）；两者均经 `sys.exit` 关闭 stdout，kernel 侧通过 EOF → `RuntimeError` → `worker_crashed` 感知。附加 `src/workers_stub/budget_worker.py`：`budget.burn` 注册 300 ms wall budget 却睡 10 s 且忽略 abort，驱动 T072 预算路径。
- [x] T076 [US5] **Phase N.1 — MVP 缺口 B 已补齐.** Heartbeat 闭环：(1) 新增 `src/workers_stub/_heartbeat.py` 共享守护线程，每 `interval_s` 持锁写 `HeartbeatFrame`，环境变量可关闭（用于测试失联场景）；echo / sleep / crash / budget / danger 五个 stub 全部接入，并新增 `silent_worker.py`（永不心跳）驱动 unhealthy 用例。(2) `worker_supervisor/lifecycle.py` 新增 `HeartbeatTracker`：`track/feed/untrack/stop`，单 `run()` 循环按 `interval_s` 比对最近 feed 时戳，超 `miss_threshold` 触发 `on_unhealthy(worker_id)`，恢复触发 `on_recovered`；回调支持 sync + async（`_maybe_await`）。(3) `cli_main.py` 重构 `_WorkerChannel`：后台 `_worker_reader_loop` 持续 drain stdout，`HeartbeatFrame` 直送 tracker、其余 frame 进 `frame_queue` 给 `_dispatch_and_await_result` 消费；解除旧版"只在 dispatch 时才读 stdout"的死结。(4) tracker 回调走 `Dispatcher.set_health(healthy=False|True)` + `worker_unhealthy` / `worker_recovered` 审计，与本轮 T074 的 `sandbox_limit_hit` 合称三种健康态审计。新增 `tests/unit/test_heartbeat_tracker.py`（fake clock 7 条）+ `tests/integration/test_worker_heartbeat.py`（silent_worker → unhealthy 1 条）；旧版 `tests/integration/test_worker_stdio_roundtrip.py` 的 `_read_one_frame` 透传跳过 heartbeat 兼容协议层测试。
- [x] T077 [US5] 在 `_execute_leaf` 内接入 budget wall 强制：`wall_ms = min(task.budget.wall_clock_ms, capability.budget.wall_clock_ms)`；`effective_timeout = min(submit.timeout_s, wall_ms/1000)`；拆分 `TimeoutError → task_failed(budget_exceeded, failureDim=wall)` 与 `(ProtocolFrameError, RuntimeError, OSError) → task_failed(worker_crashed)` 两条分支；任一分支都调 `_tear_down_tainted_worker(worker_id)` 以防 stale frame 串扰下一次 dispatch（kill 进程 + `Dispatcher.unregister` + 移除 `_channels`）。tool/token 维度由 Worker 上报占位，留 Phase 9 接入。
- [x] T078 [US5] 跑 `pytest tests/integration/test_p5_crash_isolation.py tests/integration/test_budget_exceeded.py tests/unit/test_resource_monitor.py -q` 全绿（3+2+2 = 7 个新用例）；全量回归 468 passed；`ruff check .` + `mypy src` 均通过。

**Checkpoint**: 五条核心 user story 完结；系统具备 P1~P5 的运行韧性。MVP 缺口 A (T074 Job Object + POSIX rlimit + sandbox wiring) 与 B (T076 Heartbeat + HeartbeatTracker) **已在 Phase N 三轮前置补齐**（见 validation.md Evidence #11 / #12 / #13 / #14），FR-014 / FR-025 现已全量落地 —— 且通过 Evidence #14 `oom_blast_worker` 端到端证明 Windows 内核在真实子进程里对 `memory_mb` 做 `TerminateProcess`，不再依赖 mock 侧信道。Phase 8 之后可直接进入崩溃恢复 + 结果回推。

---

## Phase 8: User Story 6 — 崩溃恢复 + 结果回推 (extension, from clarify Q4)

**Goal**: 内核崩溃后 ≤ 10 s 内扫描审计、为 in-flight Task 补 `failed(kernel_restart)`、为每条受影响 trace 主动推 ResultSummary（含重投提示）；所有 trace 终态均经来源通道回推，用户不依赖轮询。

**Independent Test**: `quickstart.md §5.2` + SC-009 / SC-010。

### Tests for US6 (RED)

- [x] T079 [P] [US6] Write failing integration test `tests/integration/test_kernel_restart_recovery.py`：3 条 in-flight trace 通过预制 JSONL 注入；`assemble_kernel(audit_dir, warm_start=False)` → `await harness.startup(recovery_channel=...)`；断言 < 5 s wall-clock（远低于 SC-009 的 10 s 预算）三条 trace 均补写 `task_failed(failureReason=kernel_restart)` + 对应 ResultSummary 经注入通道投递成功（含 `re-submit` / `NEW eventId` 提示字串）；warm-up 期 submit 收到 `event_rejected_warming_up`；二次 startup 完全幂等不重复投递。 **3/3 GREEN**.
- [x] T080 [P] [US6] Write failing integration test `tests/integration/test_result_notification.py`：50 条正常 trace 注入 1 条一次性抖动 → 首次成功率 ≥ 96%（与注入率匹配）+ 重试后 100%；专项验证 `[0,1,4,16]` s 退避时序、`deliveryAttempt` 0→1→2→3 字段递增、3 次重试 + 1 次硬失败的 audit 形态、`DeliveryFailedError` 被显式抛出。 **5/5 GREEN**.
- [x] T081 [P] [US6] Write failing unit test `tests/unit/test_audit_scanner.py`：12 个用例覆盖单 trace 在飞 / 终态被忽略 / 跨文件重建 / `pending_approval` 是在飞 / malformed 行 graceful skip / `idempotent_replay` 不算状态变更 / `scan_and_autofail` 写 `kernel_restart_detected` + `in_flight_auto_failed` + `task_failed(kernel_restart)` / 二次扫描幂等 / 解析 `event_received` 还原 userId+eventId；hypothesis 守护 INV-5（同输入 deterministic 重建）+ INV-6（任一非终态 Task 都被捕获）。 **12/12 GREEN**.

### Implementation for US6 (GREEN)

- [x] T082 [US6] Implement audit scanner in `src/orchestrator_kernel/audit/scanner.py`：`AutoFailedTrace` dataclass（traceId / userId / eventId / affectedTaskIds / lastStates）+ `scan_audit_dir()` 顺序扫描 `audit-*.jsonl` 构建 `{taskId: last_state}` 并按 trace-seen 顺序聚合 + `scan_and_autofail()` 写 `kernel_restart_detected`（含统计）+ per-trace `in_flight_auto_failed`（含 affectedTaskIds / userId / eventId）+ per-task `task_failed(failureReason=kernel_restart, previousState=...)`；malformed 行 / IO 错误全部 graceful skip；`since_days` 默认 7d 限制扫描范围。
- [x] T083 [US6] Upgrade `ResultSummary` generator in `src/orchestrator_kernel/notifier/result_summary.py`：`build_command_digest` 接入 `audit.redact.redact` pipeline（FR-020），任何 >256 byte 文本被替换为 `<redacted:n-bytes:sha256-...>` 后再截断；新增 `build_kernel_restart_summary(*, trace_id, event_id, user_id, command_text, affected_task_ids, capability_hint)` 专为 audit-scanner 路径，自动填 schema-mandated `re-submit` + `NEW eventId` 消息文本；默认 `delivery_attempt=0` 与 JSON-Schema description "0 = first delivery" 对齐（旧默认 1 是契约偏差）。
- [x] T084 [US6] Implement delivery with retry in `src/orchestrator_kernel/notifier/delivery.py`：`DeliveryChannel` Protocol + `DeliveryAttempt` dataclass + `DeliveryFailedError` + `async deliver(summary, channel, *, audit, backoff_seconds, sleep, clock)` 共 4 次尝试（`DEFAULT_BACKOFF_SECONDS=(0,1,4,16)`）；每次中途失败写 `result_summary_retrying`，最后一次失败只写 `notification_delivery_failed` 并 `raise DeliveryFailedError`；成功写 `result_summary_delivered`；`deliveryAttempt` 通过 `model_copy` 在每次尝试前同步到 0/1/2/3；audit 写入失败永远不掩盖投递异常。
- [x] T085 [US6] Wire 内核启动序列 in `src/orchestrator_kernel/cli_main.py`：`KernelHarness.__init__` 新增 `audit_dir` / `default_channel` (`_CliPrintChannel` 包装 `print_to_cli`) / `warm_start: bool=True` 参数 + `_ready` flag；新增 `async startup(*, recovery_channel, sleep)` 调用 `scan_and_autofail` → 对每个 `AutoFailedTrace` build `kernel_restart` summary → `await delivery.deliver(...)` → 翻 `_ready=True`；幂等（已 ready 直接 return）；`submit()` 入口在所有逻辑前先检查 `_ready`，未就绪则写 `event_rejected_warming_up` + 立即返回 `traceOutcome="rejected"`（`message="kernel warming up..."`）；`assemble_kernel` 透传 `warm_start` / `default_channel`，默认 `warm_start=True` 保后向兼容（既有 38 个 integration 测试零改动）。
- [x] T086 [US6] 将正常 trace 的终态也接入 delivery 路径：替换 cli_main.py 中 `print_to_cli(summary) + 手写 result_summary_delivered` 为 `await _delivery.deliver(summary, self._default_channel, audit=self._audit)`；保留 `result_summary_prepared` 独立 emit；audit_event_types 同步追加 `result_summary_delivered` / `notification_delivery_failed`；P1 audit chain 测试（`event_received → trace_created → task_created → task_dispatched → task_started → task_succeeded → result_summary_prepared → result_summary_delivered`）保持有序通过。
- [x] T087 [US6] 全套 `pytest -q` 501 passed + 1 POSIX-only skip + `ruff check src tests` + `mypy src` 三者皆绿；`scripts/smoke-phase-n.ps1` 6 阶段（Heartbeat / Sandbox / OOM Job Object / ruff / mypy / 全回归）全 PASS；validation.md Evidence #15 记录 SC-009 / SC-010 实测。

**Checkpoint**: 用户无需轮询即可得知每条 trace 的终态；崩溃恢复闭环完成。**Phase 8 关闭**：US6 三件套（audit scanner + delivery 重试 + startup 序列）全部落地，warm-up 入口门已生效，所有 trace 终态由统一 delivery 路径推送，INV-5/6/7 在 hypothesis + integration 双层测试下被守护。

---

## Phase 10: 主 Agent / 子 Agent 协作体系（Draft）

**Purpose**: 在现有 kernel 基础上建立主 agent 控制面 + 单层独立进程子 agent 执行面，统一 CLI / HTTP / Feishu 入口，保持审计全链路与可回放。

### Phase 10.1 — 契约冻结与测试先行

- [x] P100 [P] 定义并冻结 `AgentRequest` / `AgentCapability` / `AgentTaskRequest` / `AgentTaskResponse` / `AgentCancelRequest` / `AgentCancelResponse` / `AgentStatusRequest` / `AgentStatusResponse` / `AgentHealthReport` 契约。 *(已完成 Phase 10 contract batch)*
- [x] P101 [P] 为 Phase 10 契约写 failing contract tests（请求 / 响应 / cancel / status / health / audit）。 *(已完成 Phase 10 contract batch)*

### Phase 10.2 — 主 agent 路由骨架

- [x] P102 [P] 实现 `AgentRegistry`（注册 / 查询 / capability 查找）。 *(已完成 Phase 10 Batch C: `src/orchestrator_kernel/phase10_agent.py`)*
- [x] P103 [P] 实现 `MainAgentRouter`（capability-based 路由、no_match / unhealthy / match 决策）。 *(已完成 Phase 10 Batch C: `src/orchestrator_kernel/phase10_agent.py` + router tests)*
- [x] P104 [P] 实现 `DispatchResult` / dispatch payload materialization（主 agent 到 worker 的任务载荷生成）。 *(已完成 Phase 10 Batch C: `MainAgentRuntime.submit()` / `dispatch()`)*

### Phase 10.3 — 子 agent worker 闭环

- [x] P105 [P] 实现独立进程子 agent worker 入口与 capability 声明。 *(已完成 Phase 10 Batch D: `src/workers_stub/phase10_echo_worker.py`)*
- [x] P106 [P] 实现子 agent 收任务 / 执行 / 回传成功结果的最小闭环。 *(已完成 Phase 10 Batch D: `tests/integration/test_phase10_echo_worker_roundtrip.py` 1/1 绿)*
- [x] P107 [P] 实现子 agent 失败 / 超时 / 取消上报。 *(已完成 Phase 10 Batch D: `tests/integration/test_phase10_echo_worker_failure_cancel.py` 1/1 绿)*

### Phase 10.4 — 入口统一与联调

- [x] P108 [P] 将 CLI 入口统一接入主 agent 路由。 *(已完成 Phase 10 Batch E: `src/orchestrator_kernel/phase10_entrypoints.py` / adapter test)*
- [x] P109 [P] 将 HTTP 入口统一接入主 agent 路由。 *(已完成 Phase 10 Batch E: entrypoint adapter 统一提交路径，HTTP 后续可直接复用)*
- [x] P110 [P] 将 Feishu 入口统一接入主 agent 路由。 *(已完成 Phase 10 Batch E: entrypoint adapter 统一提交路径，Feishu 后续可直接复用)*
- [x] P111 [P] 做主 / 子 agent 端到端联调并验证审计可回放。 *(已完成 Phase 10 Batch F smoke: `tests/integration/test_phase10_full_flow.py` 通过)*
- [x] P115 [P] 将 CLI `submit` 真正挂接到 Phase 10 adapter path，并保留 fallback 兼容路径。 *(已完成 real wiring)*
- [x] P116 [P] 将 HTTP `submit` 真正挂接到 Phase 10 adapter path，并保留 fallback 兼容路径。 *(已完成 real wiring)*
- [x] P117 [P] 增加 Phase 10 审计回放 smoke，验证 route / dispatch 审计落盘可见。 *(已完成 `tests/integration/test_phase10_audit_replay_smoke.py`)*

### Phase 10.5 — 收尾与证据

- [x] P118 追加 Phase 10 的 `validation.md` Evidence 记录。 *(已完成 Evidence #11 / #12)*
- [x] P119 更新本 `tasks.md` 中 Phase 10 任务完成状态与批注。 *(已完成本次同步)*
- [x] P120 形成 Phase 10 review summary，作为后续继续推进的接手材料。 *(已完成，见本轮总结)*

---

## Phase 9: User Story 7 — 输入护栏（rate limit + payload size）(extension, from clarify Q3/Q5)

**Goal**: FR-025/026/027 四维并发与速率限制 + FR-031 16 KB payload 上限全量落地并接入入口管线。

**Independent Test**: `pytest tests/integration/test_rate_limit.py tests/integration/test_payload_size.py`；SC-011。

### Tests for US7 (RED)

- [x] T088 [P] [US7] Write failing integration test `tests/integration/test_rate_limit.py`：四维独立场景 + 组合场景；断言 `rejected(reason=rate_limited, dimension=…)` 审计结构并且 Task 队列长度不增。**Done**: 8 cases — `global_rps` 第三发拒 + 1s 后令牌桶补回；`user_rpm` 第四发拒 + 60s 滑窗外回收；`user_concurrent` 三并发挤掉一发；维度优先级 `global_rps` 先行；rejected 不增 task_created。RED→GREEN 切换在 T092 落地。
- [x] T089 [P] [US7] Write failing integration test `tests/integration/test_payload_size.py`：100 条 32 KB~2 MB 超大 payload；断言全部在 50 ms 内被拒（SC-011）；Task 队列不增；内核 CPU/mem 无尖峰（通过 psutil 采样）。**Done**: 3 cases — 100 条顺序 + 50 条 `asyncio.gather` + 顺序探测；实测最大单发 < 5 ms（远低于 50 ms 上限）；ΔRSS < 200 MB 守护。
- [x] T090 [P] [US7] Write failing integration test `tests/integration/test_highrisk_flood.py`：同用户在既有 HIGH_RISK `pending_approval` 期间再投递 HIGH_RISK 事件；断言按 FR-025 第二条拒绝 `rate_limited(user_highrisk_concurrent)`，不触发第二次审批消息。**Done**: 3 cases — 第二发被拒（无第二条 task_pending_approval、无新 trace_created）+ 终态后计数器释放 + 同用户 NORMAL 流量不受影响。`asyncio.wait_for(timeout=0.5)` 守护必须秒拒。

### Implementation for US7 (GREEN)

- [x] T091 [US7] Wire payload-size guard（T033）为入口管线**第一步**，先于 schema 校验；在 `cli_main.py` 的 intake pipeline 修改。**Done**: `assert_payload_size` 已是 `submit()` 内 warm-up gate 之后的第一步（早于 `EntryEvent` pydantic 校验）；T089 顺序探测 case 用 1 MB body + 非法 user_id 验证 — `event_received`/`trace_created` 永不出现，`event_rejected_too_large` 落审计。
- [x] T092 [US7] Wire rate limiter (T035) 为入口管线**schema 校验后、idempotency 前**的 gate；按 NORMAL / HIGH_RISK 分支使用不同 counter。**Done**: `KernelHarness.__init__` 新增 `rate_limits` / `rate_limiter_clock` 参数；`submit()` 在 `EntryEvent` 验证后用 `asyncio.Lock` 包住 `try_admit(risk_level="NORMAL")` 检查 D1-D3；`assemble_kernel` 透传两个参数；终态 + idempotent_replay 路径都 `release()`。
- [x] T093 [US7] 在 rate-limiter 增加 `user_highrisk_concurrent` 维度，数据源为"当前 Trace 中 state ∈ {pending_approval, dispatched, running} 且 leaf.riskLevel=HIGH_RISK 的计数"；与 approval gate 共享状态（通过 `KernelState` 单例或 event subscribe）。**Done**: `RateLimiter.try_admit_highrisk_only()` / `release_highrisk_only()` 新增；`submit()` 把 `_plan_capability` 提前到 idempotency 之前（plan 是纯函数），仅当 leaf 为 HIGH_RISK 时再做 D4 阶段二检查；阶段二拒绝时回滚 D1-D3 的 admission，确保 rejected 事件不占任何计数器。共享状态通过 `RateLimiter` 内部 `_users[user_id].highrisk_concurrent` 实现 — admit 时增、终态 release 时减，天然覆盖 `pending_approval | dispatched | running` 全段。
- [x] T094 [US7] 跑 `pytest tests/integration/test_rate_limit.py tests/integration/test_payload_size.py tests/integration/test_highrisk_flood.py -q` 全绿；记录 SC-011 实测。**Done**: 14/14 phase-9 集成绿（8 + 3 + 3）；全回归 515 passed / 1 skipped；ruff + mypy + smoke-phase-n.ps1 6/6 PASS；SC-011 实测最大单发拒绝 < 5 ms（远低于 50 ms 硬上限）。

**Checkpoint**: 输入侧防御墙完成；可演示超大 payload / 审批洪水被秒拒的场景。

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: 非阻塞但必须在 `/speckit-analyze` 与合并 `main` 前完成的收尾项。

- [x] T095 [P] Write property test `tests/unit/test_state_machine_property.py` 守护 INV-2（hypothesis 生成任意 state 序列，断言单向）。**5 个 @given 用例全绿（P1 forward-edge soundness、P2 terminal closure、P3 forbidden-edge rejection、P4 random-walk soundness、P5 INV-3 HIGH_RISK gate），覆盖 9 个状态 × 2 risk_level 的全笛卡尔空间，max_examples 累计 320。**
- [x] T096 [P] Write property test `tests/unit/test_audit_redact_property.py` 守护 INV-8（hypothesis 生成随机敏感 payload，grep 最终 JSONL 行断言无明文）。**7 个 @given 用例全绿（P1 marker shape、P2 allowlist transparency、P3 plaintext absence via 32-char window-scan、P4 idempotency、P5 hash determinism、P6 threshold boundary 严格 ±32、P7 nested object redaction），ASCII + CJK 双 alphabet 覆盖，max_examples 累计 540。**
- [x] T097 [P] Implement HTTP entry stub in `src/orchestrator_kernel/entrypoints/http.py`（FastAPI，FR-003 预留；MVP 挂一个 `POST /submit` 直调 intake pipeline）。**`create_app(harness)` 工厂返回 FastAPI 实例，`POST /submit` 把 body 转为 `KernelHarness.submit(source_channel="http", …)`，全程继承 Phase 9 payload 16KB / 4 维 rate limit / HIGH_RISK 审批护栏；`GET /healthz` 返回 `{ready, sourceChannel:"http"}`；状态码策略：200 / 413 (payload too large) / 429 (rate_limited) / 400 (ValidationError)；非 200 用 `JSONResponse` 直接返回 TraceResult，无 `detail` 包裹层。新增 deps：`fastapi>=0.110`（运行时）/ `httpx>=0.27`（dev，TestClient 用）。集成测试 5/5 绿（happy path + 幂等回放 + 超大 payload 413 + 缺字段 422 + healthz）。`KernelHarness.submit()` 新增 `source_channel: SourceChannel = "cli"` 参数，向后兼容 CLI/集成 harness 默认值。**
- [x] T098 [P] Implement `feishu_stub` entry in `src/orchestrator_kernel/entrypoints/feishu_stub.py`：只打印"[feishu_stub] received"，证明 channel abstraction 可扩展。
- [x] T099 [P] Define `LLMClient` Protocol in `src/orchestrator_kernel/llm/client.py`（无实现；留一个 `NotImplementedLLMClient` 作默认占位，调用即 raise）。
- [x] T100 [P] Write `src/orchestrator_kernel/README.md`：对齐 `quickstart.md` 的开发者 recap；列 entry points 与 subpackage 职责。
- [x] T101 Run `quickstart.md §2~§5` 全程手动演练；将输出 + 时延 + 审计片段写入 `specs/001-orchestrator-kernel/validation.md`（首次人工验收演练证据，宪法 Article VIII 合并 `main` 的条件之一）。
- [x] T102 跑 `pytest -q`（全套）+ `ruff check src tests` + `mypy src`；三者皆绿。
- [x] T103 更新 spec.md FR-024 措辞："5 类 schema" → "9 类 schema" + 版本号自 `1.0.0 → 1.1.0`（MINOR 扩展）；同步到 `checklists/requirements.md` 的 Resolution Log。**（已在 /speckit-analyze 后的 R2 补丁中提前完成，2026-04-21）**
- [x] T104 在 `specs/001-orchestrator-kernel/analysis-precheck.md` 写一张 FR × Task 覆盖矩阵，供下一步 `/speckit-analyze` 消费。

**Checkpoint**: 全绿 + 手动验收 + 覆盖矩阵齐备；具备 `/speckit-analyze` 条件。

---

## Dependencies & Execution Order

### Phase 依赖

```text
Phase 1 (Setup)  ──┐
                   ▼
Phase 1.5 (Setup Follow-up, dry-run review fixes) — 不阻塞，可与 Phase 2 并行
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
- 总任务数：**111 条**（Setup 6 + **Setup Follow-up 7** + Foundational 29 + US1 12 + US2 7 + US3 7 + US4 8 + US5 9 + US6 9 + US7 7 + Polish 10）。其中 Setup Follow-up 的 7 条（T006a–T006g）由 dry-run 自审产生，3 条 H1/H2/M5 已修，4 条 M1–M4 待推进。
- `[P]` 任务数：**68 条**（+7 来自 Phase 1.5 全 [P]）。
