# Spec-Plan-Harness

> 飞书下发命令 → 主 Agent 编排 → 子 Agent 操控电脑
> 的多 Agent 自动化协同办公系统（与 OpenClaw / OpenManus / Anthropic Computer Use 思路类似）。

本仓库采用**规范驱动开发（Spec-Driven Development）**，规范先行，代码随后；
使用 [GitHub Spec Kit](https://github.com/github/spec-kit) 作为规范工作流。

## 开发阶段（按顺序）

1. `/speckit-constitution` —— 立"项目宪法"，钉死非功能红线（安全/审计/沙箱/人机协同门）
2. `/speckit-specify` —— 写功能规范（what & why，不谈技术栈）
3. `/speckit-clarify` —— 反问消歧，逐项补齐模糊点
4. `/speckit-plan` —— 落技术方案（语言、消息总线、LLM、Computer Use 能力等）
5. `/speckit-tasks` —— 拆解为可执行任务
6. `/speckit-checklist` —— 质量检查单
7. `/speckit-analyze` —— 三件套一致性审计
8. `/speckit-implement` —— 正式动工（Phase 1 Setup 已落地；Phase 2 Foundational 排队）

## 当前 MVP 范围

**先做主 Agent 编排内核**（feature `001-orchestrator-kernel`）：
飞书适配与桌面操控子 Agent 作为"替身 stub"存在，
确保编排内核的消息协议、任务生命周期、审计日志可单独验证。

飞书接入与真实桌面操控子 Agent 放到后续迭代。

---

## Quickstart for developers

> Phase 1 Setup 已落地。新克隆仓库后，**5 条命令**走完质量基线。
> Windows 10 + PowerShell 5.1 为主力开发环境（详见 `.specify/memory/constitution.md` Additional Constraints）。

### 1. 安装 `uv`（一次性）

```powershell
# 官方一行安装；装完后 uv 在 %USERPROFILE%\.local\bin\
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# 把它加到当前会话 PATH
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

### 2. 装依赖（39 包，约 1.5 秒）

```powershell
uv sync
```

`uv sync` 会自动读 `pyproject.toml` 的 `[dependency-groups].dev`（PEP 735）并把 runtime + dev deps 全装进 `.venv/`。

> 如果你是纯 pip 用户（pip ≥ 25）：`pip install --group dev -e .`；pip < 25 请手动装
> `pytest pytest-asyncio hypothesis ruff mypy` 五包。

### 3. 跑质量基线（4 条命令都应 0 error）

```powershell
uv run ruff check src tests   # lint：E/F/I/W/UP/B，100 字符行宽
uv run mypy src                # type：strict + pydantic.mypy
uv run pytest -q               # test：Phase 1 期只有 3 条 scaffold smoke
uv build                       # 打包：dist/*.whl 不含 workers_stub/
```

### 4. 启动开发循环

阅读 `specs/001-orchestrator-kernel/tasks.md` 找到下一条待办（`[ ]`），按 TDD 顺序"先写失败测试 → 再写最小实现 → 再重构"；
每条完成后单独（或按小簇）commit 到 `hjx` 分支，commit message 包含"变更点 / 影响范围 / 测试结论"。

### 5. 验证证据（谁动过什么）

- `specs/001-orchestrator-kernel/validation.md` —— 每一次阶段性绿灯的可审计证据
- `specs/001-orchestrator-kernel/tasks.md` —— 111 条任务的打勾进度表

---

## 工件位置

- 宪法：`.specify/memory/constitution.md`（8 条核心原则，含 `main`/`hjx` 双分支门禁）
- 当前功能工件：`specs/001-orchestrator-kernel/` (`spec.md` + `plan.md` + `research.md` + `data-model.md` + `contracts/` 9 份 JSON Schema + `tasks.md` + `quickstart.md` + `validation.md`)
- 模板：`.specify/templates/`
- PowerShell 脚本：`.specify/scripts/powershell/`
- Cursor skills：`.cursor/skills/speckit-*/`
- Cursor 规则：`.cursor/rules/specify-rules.mdc`

## 分支策略（宪法 Article VIII）

- `main` —— 生产/可信分支，**禁止**直接 push，**禁止** force-push
- `hjx` —— 开发/集成分支，所有开发先到此；每次 commit message MUST 含"更新说明"
- 合并 `hjx → main` 必须满足：`/speckit-analyze` 通过 + TDD 全绿 + 人工验收演练 + SemVer tag
