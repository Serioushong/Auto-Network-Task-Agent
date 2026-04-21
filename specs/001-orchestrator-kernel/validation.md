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
