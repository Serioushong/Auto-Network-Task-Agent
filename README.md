# Spec-Plan-Harness

> 飞书下发命令 → 主 Agent 编排 → 子 Agent 操控电脑
> 的多 Agent 自动化协同办公系统（与 OpenClaw / OpenManus / Anthropic Computer Use 思路类似）。

本仓库**前期只做规范与设计（Spec-Driven Development）**，暂不落地代码。
使用 [GitHub Spec Kit](https://github.com/github/spec-kit) 作为规范工作流。

## 开发阶段（按顺序）

1. `/speckit-constitution` —— 立"项目宪法"，钉死非功能红线（安全/审计/沙箱/人机协同门）
2. `/speckit-specify` —— 写功能规范（what & why，不谈技术栈）
3. `/speckit-clarify` —— 反问消歧，逐项补齐模糊点
4. `/speckit-plan` —— 落技术方案（语言、消息总线、LLM、Computer Use 能力等）
5. `/speckit-tasks` —— 拆解为可执行任务
6. `/speckit-checklist` —— 质量检查单
7. `/speckit-analyze` —— 三件套一致性审计
8. `/speckit-implement` —— 正式动工（本阶段暂不启用）

## 当前 MVP 范围

**先做主 Agent 编排内核**：
飞书适配与桌面操控子 Agent 作为"替身 stub"存在，
确保编排内核的消息协议、任务生命周期、审计日志可单独验证。

飞书接入与真实桌面操控子 Agent 放到后续迭代。

## 工件位置

- 宪法：`.specify/memory/constitution.md`
- 模板：`.specify/templates/`
- PowerShell 脚本：`.specify/scripts/powershell/`
- Cursor skills：`.cursor/skills/speckit-*/`
- Cursor 规则：`.cursor/rules/specify-rules.mdc`
