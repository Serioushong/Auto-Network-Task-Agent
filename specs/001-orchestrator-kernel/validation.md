# Orchestrator Kernel MVP — Validation Evidence Log

**Purpose**: 记录每一次阶段性验收的可审计证据（命令 + 版本 + 输出摘要 + UTC 时间戳），
对齐宪法 Article VIII（合并 `main` 需要手动验收演练证据之一）。

**Rules**:
- 每一行 = 一次原子验收证据；追加写，禁止修改既有行。
- 每条证据 MUST 标注：date (UTC ISO-8601)、对应 task ID、命令、关键输出、结论。
- 对应 `tasks.md` T101（首次人工验收演练）与 T006（首个 pytest 绿灯）。

---

## Evidence #1 — T006 scaffold green (Phase 1 Setup checkpoint)

- **UTC**: 2026-04-21T04:51:48Z
- **Host**: Windows 10 (19045), PowerShell 5.1
- **Toolchain**: `uv 0.11.7`, `Python 3.13.5`
- **Task**: T006 (dry-run 批次，覆盖 T001–T006)
- **Commands**:
  - `uv sync` → Installed 39 packages in 1.49s（pydantic 2.13.3 / anyio 4.13.0 / structlog 25.5.0 / typer 0.24.1 / psutil 7.2.2 / jsonschema 4.26.0 / ulid-py 1.1.0 / pytest 9.0.3 / pytest-asyncio 1.3.0 / hypothesis 6.152.1 / ruff 0.15.11 / mypy 1.20.1 + editable install `orchestrator-kernel==0.1.0`）
  - `uv run pytest -q` → `3 passed in 0.14s`（`tests/test_scaffold.py` 3 条；exit code 0）
- **Scope of test**: 仅 scaffold smoke（包可导入、7 个子包齐备、`workers_stub` 可导入、`__version__` 存在且 semver）
- **Exclusion**: 无任何契约测试 / 单元测试 / 集成测试；Phase 2（T007+）尚未启动
- **Artifacts created** (committed to `hjx`):
  - `pyproject.toml`（项目 + 依赖 + ruff/mypy/pytest/hypothesis 配置 + 控制台脚本 + `dependency-groups.dev`）
  - `src/orchestrator_kernel/` + 7 子包 + `cli_main.py` 占位（9 个 `__init__.py`）
  - `src/workers_stub/`（1 个 `__init__.py`）
  - `tests/{contract,integration,unit}/` + root `conftest.py`（`tmp_audit_dir` fixture）
  - `tests/test_scaffold.py`（3 条 smoke 测试）
  - `.gitignore` 新增 `var/` + 工具缓存 + `dist/` / `build/`
- **Conclusion**: ✅ Phase 1 Setup checkpoint PASSED. 具备启动 Phase 2 Foundational（T007 契约测试 RED 开始）的条件。
- **Next gate**: 用户批准 Phase 2 启动后进入 T007–T035（9 份契约测试 + 9 份 pydantic 镜像 + 审计 / 状态机 / 限流 / payload 护栏）。

---

## Evidence #2 — Phase 1.5 fix-and-commit (T006a/T006b/T006c)

- **UTC**: 2026-04-21T05:10:00Z（自审复盘后立即修复）
- **Trigger**: 用户指令 "你自己重新 REVIEW 一遍" → 自审报告挖出 H1/H2 + M1–M5 共 7 条 finding；用户批 `fix-and-commit`。
- **Tasks closed**: T006a (H1)、T006b (H2)、T006c (M5)；M1–M4 写入 Phase 1.5 后续 task (T006d–T006g)。
- **Commands**:
  - `uv run ruff check src tests` → **All checks passed!**（修前 2 errors → 修后 0）
  - `uv run mypy src` → **Success: no issues found in 10 source files**（修前后保持）
  - `uv run pytest -q` → **3 passed in 0.15s**（修前后保持）
  - `Select-String pyproject.toml -Pattern "optional-dependencies"` → **0 matches**（H2 去重彻底）
- **Diff summary**:
  - `src/orchestrator_kernel/kernel/__init__.py` 单行 docstring → 4 行；`cli_main.py` `help=` 提取为 `_HELP` 常量并删除多余 `_root` 空 callback。
  - `pyproject.toml` 删除 `[project.optional-dependencies].dev`（5 行）；`[dependency-groups]` 上方加 4 行注释解释 PEP 735 + pip 用户 fallback。
  - `tests/test_scaffold.py` 顶部 docstring 扩为 18 行 lifecycle 说明（标注 ⚠️ TRANSITIONAL）。
  - `tasks.md` 新增 Phase 1.5（7 条 T006a–T006g），更新 Phase 依赖图与统计（104 → 111 条 / 61 → 68 [P]）。
- **Conclusion**: ✅ Phase 1 Setup baseline 4 gate（ruff / mypy / pytest / no-duplicate-deps）全绿。M1–M4（workers_stub wheel 排除、tests `__init__` 合规、`.gitattributes`、README quickstart）作 Phase 1.5 follow-up 持有，**不阻塞 Phase 2 启动**。
- **Next gate**: 同 Evidence #1。

---

## Evidence #3 — Phase 1.5 完工 (T006d/T006e/T006f/T006g)

- **UTC**: 2026-04-21T05:45:00Z
- **Trigger**: 用户指令 `finish-1.5`，要求 Phase 1 零债务收尾后再进 Phase 2。
- **Tasks closed**: T006d (M1)、T006e (M2)、T006f (M3)、T006g (M4)。Phase 1.5 七条全绿。
- **Commands & results**:
  - `uv sync` → Resolved 39 packages in 1ms / Checked 39 packages in 1ms（无新装）
  - `uv run ruff check src tests` → **All checks passed!**
  - `uv run mypy src` → **Success: no issues found in 10 source files**
  - `uv run pytest -q` → **3 passed in 0.15s**
  - `uv build` → Successfully built `dist/orchestrator_kernel-0.1.0.tar.gz` + `dist/orchestrator_kernel-0.1.0-py3-none-any.whl`
  - `python -m zipfile -l dist/*.whl` → **13 entries, workers_stub/ 0 entries** ✅（T006d verification 通过）
- **Diff summary**:
  - `pyproject.toml`: `[tool.hatch.build.targets.wheel].packages` 由 `["src/orchestrator_kernel", "src/workers_stub"]` 改为 `["src/orchestrator_kernel"]` + 3 行注释解释。
  - `specs/001-orchestrator-kernel/research.md`: 新增 **R-11 workers_stub 包 layout 与 wheel 打包策略**（18 行 Decision/Rationale/Alternatives/Verification + Cross-reference 表追加 R-11 行）。
  - `tests/__init__.py`、`tests/contract/__init__.py`、`tests/integration/__init__.py`、`tests/unit/__init__.py`：**4 份删除**（pytest 官方推荐 namespace 包）。
  - `tests/conftest.py`: 顶部 docstring 新增 6 行 layout note 说明"故意不含 `__init__.py`"。
  - `.gitattributes`: 新建 49 行（`* text=auto eol=lf` + 扩展名白名单 + Windows 专用 `*.ps1` CRLF + binary + `uv.lock` 标 generated）。
  - `README.md`: 从 35 行 → 约 80 行，新增"Quickstart for developers"5 步（装 uv / `uv sync` / 4 条质量基线命令 / TDD 入口 / 验证证据）+ "工件位置" 扩展（9 份 contracts 显式列出）+ "分支策略（宪法 Article VIII）"。
  - `specs/001-orchestrator-kernel/tasks.md`: T006d–T006g 4 条从 `[ ]` → `[x]`，每条加"已完成 finish-1.5 批次"备注；Checkpoint 改为"零债务收尾"。
- **Wheel inspection verbatim**:
  ```
  orchestrator_kernel/__init__.py                  119 bytes
  orchestrator_kernel/cli_main.py                  882 bytes
  orchestrator_kernel/audit/__init__.py            80 bytes
  orchestrator_kernel/contracts/__init__.py        102 bytes
  orchestrator_kernel/entrypoints/__init__.py      75 bytes
  orchestrator_kernel/kernel/__init__.py           141 bytes
  orchestrator_kernel/llm/__init__.py              90 bytes
  orchestrator_kernel/notifier/__init__.py         75 bytes
  orchestrator_kernel/worker_supervisor/__init__.py 94 bytes
  orchestrator_kernel-0.1.0.dist-info/*            METADATA / WHEEL / entry_points / RECORD
  ```
- **Conclusion**: ✅ Phase 1 Setup **零债务收尾**。baseline 5 gate（ruff / mypy / pytest / no-duplicate-deps / wheel-excludes-stub）全绿。
- **Next gate**: 用户批准 Phase 2 Foundational 启动 → `/speckit-implement T007`（`tests/contract/test_entry_event.py` RED 先写）。

---

## Evidence #4 — Phase 2 Foundational 完成 (T007~T035)

- **UTC**: 2026-04-21T06:30:00Z
- **Trigger**: 用户指令 `go-P2`，要求 Phase 2 Foundational 29 条任务一次性推进（2A-RED / 2B-GREEN / 2C-infra）。
- **Tasks closed**: T007~T015（9 份契约测试 RED）、T016~T024（9 份 pydantic 镜像 GREEN）、T025（round-trip）、T026~T035（config + hasher + redact + state machine + audit writer + payload guard + rate limiter，含 4 对 RED→GREEN TDD）。
- **Sub-commit trail**:
  - `26a0732` test(001-orchestrator-kernel): 2A-RED 9 份契约测试落地 (T007~T015) → **9 errors during collection**（预期 RED，ImportError）
  - `a7e3705` feat(001-orchestrator-kernel): 2B-GREEN 9 份 pydantic 契约镜像 + round-trip (T016~T025) → **348 passed**
  - 本 commit feat(001-orchestrator-kernel): 2C-infra T026~T035 shared infra → **406 passed**（累计 +58 新测试：29 state machine + 6 audit writer + 8 payload size + 12 rate limit + 3 contract 调整）
- **Final commands & results**:
  - `uv run ruff check src tests` → **All checks passed!**（累计 --fix 自动规整 42 处 style 差异：import 顺序 / typing_extensions→typing / quoted-self-ref / datetime.UTC alias / F401 未用导入）
  - `uv run mypy src` → **Success: no issues found in 26 source files**（9 contracts + 3 audit + 4 kernel + 1 config + scaffold 占位）
  - `uv run pytest -q` → **406 passed in 0.59s**
  - 分解：contract 348 passed（含 round-trip 18）+ unit 55 passed（state_machine 29 + audit_writer 6 + payload_size 8 + rate_limit 12）+ scaffold 3 passed
- **Diff summary (本批 2C-infra commit)**:
  - `src/orchestrator_kernel/config.py`（114 行）：pydantic-settings KernelConfig，TOML optional；`RateLimitDefaults`、`approval_timeout_default_ms=600_000`、`payload_max_bytes_default=16_384`、`system_hard_cap` 越硬顶启动即 raise。
  - `src/orchestrator_kernel/audit/hasher.py`（33 行）：`sha256_trunc16` + `hash_canonical_json`，32 hex chars 对齐 `^[0-9a-f]{32}$` pattern。
  - `src/orchestrator_kernel/audit/redact.py`（95 行）：structlog processor 工厂，allowlist + 256 字节阈值双策略；大字符串/嵌套对象替换为 `<redacted:N-bytes:sha256-xxx>`。
  - `src/orchestrator_kernel/audit/writer.py`（118 行）：AuditWriter JSONL append + UTC 日切 + 磁盘失败 `healthy=False` 降级 + `disk_write_failed` 自审计；clock + opener 两重 DI 支持测试。
  - `src/orchestrator_kernel/kernel/state_machine.py`（88 行）：`ALLOWED_TRANSITIONS` 表 + `transition()` 纯函数；INV-2（终态零出边）与 INV-3（HIGH_RISK 强制走 pending_approval）硬断言；通过 `Task.model_validate(dump | updates)` 触发 Task @model_validator 再次校验。
  - `src/orchestrator_kernel/kernel/validators.py`（106 行）：`PayloadTooLarge` 异常 + `assert_payload_size()`（UTF-8 字节数，先于 schema 跑）+ `build_too_large_audit()`（用户 ID 不满足 actor pattern 时自动降级 `actor="system"` 并把原值进 extra）。
  - `src/orchestrator_kernel/kernel/rate_limit.py`（124 行）：`RateLimiter` 四维（token bucket 全局 rps / sliding window user rpm / 两个 counter user concurrent + user highrisk concurrent），clock DI，`try_admit` + `release` 对偶 API。
  - `tests/unit/test_state_machine.py`（144 行，29 tests）；`tests/unit/test_audit_writer.py`（123 行，6 tests）；`tests/unit/test_payload_size.py`（88 行，8 tests）；`tests/unit/test_rate_limit.py`（139 行，12 tests）。
- **TDD 迭代痕迹（诚实披露）**:
  - 2A→2B 切换时 5 条 test failure：(1) `kind` 字段默认值导致 pydantic 视为 optional 与 schema required 冲突（修复：移除 9 处默认值）；(2) 误写的 `user:alice@local` actor 违反 schema pattern；(3) T025 required-parity 测试连带失败。二轮修复后 345 passed。
  - 2C-4 rate limit 2 条 test failure：误用同一 userId 循环 50 次，在第 11 次触达 `user_concurrent`（=10）而非 `global_rps`（=50）。测试逻辑修正为 50 个不同 userId 后全绿。
- **Phase 2 Checkpoint**: ✅ **契约与基础设施就位**。可以启动 Phase 3~9 的 user stories；所有 user stories 并行就绪（契约层、状态机、审计、限流、payload 护栏皆已可注入）。
- **Next gate**: 用户批准 Phase 3 启动 → `/speckit-implement T036`（`tests/integration/test_p1_basic_loop.py` RED 先写；echo-worker 闭环 MVP）。

---

## Evidence #5 — Phase 3 US1 MVP 完整闭环 (T036~T047)

- **UTC**: 2026-04-21T08:15:00Z
- **Trigger**: 用户指令 "继续 001-orchestrator-kernel Phase 3B-GREEN 批次 2（T043~T047）"；上一批 3B-GREEN 批次 1 已落 T039~T042（task_tree / dispatcher / supervisor / protocol codec）。
- **Tasks closed**: T043（echo-worker stub）、T044（entrypoints/cli.py typer submit）、T045（cli_main assemble_kernel + KernelHarness）、T046（notifier.result_summary）、T047（全套 pytest 绿 + p95 记录）。
- **Commands & results**:
  - `.venv\Scripts\ruff.exe check src tests` → **All checks passed!**
  - `.venv\Scripts\mypy.exe src` → **Success: no issues found in 33 source files**
  - `.venv\Scripts\pytest.exe tests/integration -q` → **10 passed in 1.69s**（test_p1_basic_loop 4 / test_p1_no_worker 3 / test_worker_stdio_roundtrip 3）
  - `.venv\Scripts\pytest.exe -q` → **416 passed in 2.13s**（Phase 2 累计 406 + Phase 3 新增 10）
  - `.venv\Scripts\orchestrator-kernel.exe submit --text "echo hello" --audit-dir var/smoke-audit` → `traceId=01KPQHH4F9N6KZF1Z54H395KGZ outcome=all_succeeded duration=0.00s` + 一行合同级 ResultSummary JSON（`kind=result_summary / traceOutcome=all_succeeded / leafResults=[{capability=echo.say,outcome=succeeded}] / message="hello"`）
- **SC-002 p95 micro-bench** (30 次 warm-path submit，echo-worker 已注册)：
  - min = 0.0015 s / median = 0.0016 s / **p95 = 0.0019 s** / max = 0.0023 s
  - 全部 30 次均 `traceOutcome=all_succeeded`
  - 预算 ≤ 3 s（spec.md SC-002）；实测 p95 相当于预算的 **0.063%**，大量余量（余量主要用于未来 LLM 规划、多 leaf、真实 Worker I/O）。
  - 冷启（首发 submit 含 spawn + register）：integration test 端到端（含 fixture teardown）仍 ≤ 1 s 内收敛。
- **Diff summary (本批 3B-GREEN 批次 2)**:
  - `src/workers_stub/echo_worker.py`（130 行新建）：独立 stdio 可执行；startup `RegisterFrame` / 响应 `dispatch` 的 `StartedFrame + ResultFrame(succeeded, output.text=payload.text)` / `shutdown` 干净退 0 / malformed JSON `continue`。
  - `src/orchestrator_kernel/notifier/result_summary.py`（162 行新建）：`build_command_digest` / `compute_trace_outcome` / `_build_message` / `build_result_summary` / `print_to_cli`；`leaf_outputs` map 把 ResultFrame 的 output 回填 message。
  - `src/orchestrator_kernel/cli_main.py`（385 行重写，从占位 30 行升级）：`KernelHarness` 类（register_worker / submit / shutdown / _execute_leaf）；`assemble_kernel(audit_dir)` async factory；planner stub `_plan_capability`（echo → echo.say，其它 → desktop.click）；`_WorkerChannel`（asyncio.Lock 守 dispatch-started-result 原子性）；8 条审计事件构造；`_map_worker_failure` 把 WorkerFailureReason 折到 Task.FailureReason。
  - `src/orchestrator_kernel/entrypoints/cli.py`（150 行新建）：Typer subapp；`submit` / `status` 命令；`Annotated[...]+typer.Option` 规避 B008；`ORCHESTRATOR_USER` env 作 `--user-id` fallback；`--worker` 可重复。
- **Audit chain verification** (test_echo_hello_audit_chain_complete)：JSONL 按序出现 `event_received → trace_created → task_created → task_dispatched → task_started → task_succeeded → result_summary_prepared → result_summary_delivered`（外加 root_intent 的 `task_created` 与 `worker_registered`，不影响顺序断言）。FR-006 / FR-019 满足。
- **No-capable-worker branch verification** (test_no_capable_worker_audit_shape)：`desktop.click` intent 下 `task_failed.extra.failureReason == "no_capable_worker"`；`result_summary_delivered` 仍然出现 → FR-029（用户不轮询仍能得知失败）满足。
- **Phase 3 Checkpoint**: ✅ **US1 MVP 独立可用**。`orchestrator-kernel submit` 命令可端到端演示；T047 要求的 integration 绿 + p95 证据齐全。
- **Next gate**: 用户批准 Phase 4 启动 → `/speckit-implement T048`（`tests/integration/test_p2_idempotency.py` RED 先写；幂等层 T051 接在 cli_main 现有管线的 "payload-size → schema → rate-limit" 之后、"trace 创建" 之前）。

---

## Evidence #6 — Phase 4 US2 幂等性保证完整闭环 (T048~T054)

- **UTC**: 2026-04-21T08:27:12Z
- **Trigger**: 用户指令 "继续执行Phase 4"；延续 Evidence #5 的 US1 MVP，本批把 INV-1（每条 EntryEvent 在内核视角下有且只有一个 Trace）从"契约声明"升级到"代码强制 + 集成证据"。
- **Tasks closed**: T048（integration RED）、T049（hypothesis `RuleBasedStateMachine` RED）、T050（unit cache RED，含 50 路 `asyncio.gather` 压测）、T051（`kernel/idempotency.py` GREEN）、T052（`cli_main.KernelHarness.submit` 接入）、T053（`event_received` + `idempotent_replay` 两类审计透传 `idempotent_replay=true`）、T054（三文件测试套件全绿）。
- **TDD 痕迹 (诚实披露)**:
  - T050 RED → GREEN：初版 `lookup_or_register` 返回的 `snapshot` 类型错配（曾返回 `dict`，测试断言 `CachedTrace` 实例），以 `@dataclass(frozen=True) CachedTrace` 统一对外类型一次修复。
  - T049 RED → GREEN：hypothesis 把 `state.check_invariants()` 要求 runtime 参数，改用 `IdempotencyMachine.TestCase` 作为 pytest 入口即 OK（hypothesis 惯用法）。
  - T048 RED：4/4 条整合测试均命中"不同 traceId"断言，完全符合 FR-022 / FR-023 未实现状态；T052 接入后 4/4 GREEN（concurrent 路径依赖 `threading.Lock` + sync API 保证 compound check-and-set 原子）。
- **Commands & results**:
  - `.venv\Scripts\ruff.exe check src tests` → **All checks passed!**
  - `.venv\Scripts\mypy.exe src` → **Success: no issues found in 34 source files**（新增 `kernel/idempotency.py`）
  - `.venv\Scripts\pytest.exe tests/integration/test_p2_* tests/unit/test_idempotency_cache.py -q` → **13 passed in 1.12s**（integration 4 + property 1 + unit 8）
  - `.venv\Scripts\pytest.exe -q` → **429 passed in 2.96s**（Phase 3 416 + 本批新增 13）
- **P2 Acceptance Scenarios 验证**:
  - **Scenario 1 (terminal replay)**: 1 fresh + 4 replays 同 `eventId=E2`，审计恰好 1 条 `task_dispatched` + 4 条 `eventType=idempotent_replay && idempotent_replay=true`（`extra.eventId` 匹配）。
  - **Scenario 2 (running / concurrent)**: `asyncio.gather` 3 路并发同 `eventId`，3 个 `traceId` 合并为 1；`task_dispatched` 计数 = 1。
  - **Scenario 3 (failed replay)**: `desktop.click` intent 首投失败（`no_capable_worker`）→ 重投同 `eventId` 返回同 `traceId` + 同 `traceOutcome=all_failed`；`task_failed` 审计计数 = 1（replay 不再二次落 `task_failed`）。
  - **Edge: 跨用户同 eventId**: `(alice, E2)` 与 `(bob, E2)` 各自独立 traceId，两次都 fresh（spec.md Edge Cases 要求）。
- **INV-1 证据 (hypothesis 50×25 步)**: `replay_count == total_lookups - unique_keys` 两条不变量在 1250 次随机步上全部持有；`trace_for_key.values()` 去重后长度不变（不存在两 key 指向同一 traceId 的情况）。
- **Diff summary (本批 Phase 4)**:
  - `src/orchestrator_kernel/kernel/idempotency.py`（158 行新建）：`CachedTrace` frozen dataclass + `IdempotencyCache` 类（`lookup_or_register` / `mark_terminal` / `restore_entry` / `__contains__` / `__len__`）；`threading.Lock` 守 compound check-and-set；ULID 工厂可注入便于测试；`restore_entry` 作为 R-03 "从审计日志重建缓存" 的挂钩。
  - `src/orchestrator_kernel/cli_main.py`（+63/-16）：`KernelHarness.__init__` 新增 `idempotency_cache` 注入点（默认内存实例）；`submit` 管线在 `EntryEvent` 构造后立即 `lookup_or_register`，replay 路径抽出 `_build_replay_result`（短路不过 task_tree / dispatcher / supervisor）；fresh 路径在终态写 `mark_terminal` 前即可被并发 replay 命中（锁保证）。
  - `tests/unit/test_idempotency_cache.py`（150 行新建，8 tests）；`tests/integration/test_p2_idempotency.py`（153 行新建，4 tests）；`tests/integration/test_p2_idempotency_property.py`（98 行新建，hypothesis 1 TestCase）。
- **FR 覆盖**:
  - FR-002 / FR-022：同 `(userId, eventId)` 重复投递必返回同一 `traceId`（8 unit + 4 integration 双证）。
  - FR-023：replay 计数 = `总投递 - 唯一 key`（hypothesis 不变量）；R-03 重建路径留 `restore_entry` 挂钩（Phase 7 落地）。
  - INV-1：hypothesis `RuleBasedStateMachine` 50×25 随机步 + 并发压测双证。
- **CLI 进程内 vs 跨进程幂等 (诚实披露)**:
  - CLI smoke `1..3 | orchestrator-kernel submit --event-id E-SMOKE-12345678 --text "echo hi"` 实测 3 次返回 3 个不同 traceId，审计 3 条 `task_dispatched`，0 条 `idempotent_replay` — 原因是当前 `IdempotencyCache` 仅内存态，每次 CLI 进程退出后失效。
  - quickstart §2.4 "5 次同 eventId 返回同一 traceId + 4 条 replay 审计" 的跨进程演示依赖 R-03（从审计日志重建缓存），明确归属 **Phase 7（audit scanner + restore）**，`IdempotencyCache.restore_entry()` 已留挂钩。
  - 本批 GREEN 的契约范围是"进程内并发/顺序重投幂等"，由 4 条 integration + 8 条 unit + 1 条 hypothesis 覆盖；spec.md FR-022 / FR-023 + INV-1 在该范围内 **PASS**。
- **Phase 4 Checkpoint**: ✅ **进程内 US2 MVP 独立可用**（`asyncio.gather` 并发 + 顺序重投均只调用一次 Worker）；跨进程 quickstart §2.4 演示阻塞于 Phase 7 R-03。
- **Next gate**: 用户批准 Phase 5 启动 → `/speckit-implement T055`（HIGH_RISK 审批门 RED；`pending_approval` → `approve` / `deny` / `timeout` 三分支）。或先补 Phase 7 T084 audit-scanner 让 CLI 跨进程 demo 转绿。

