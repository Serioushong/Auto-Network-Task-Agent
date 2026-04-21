# Contracts — Orchestrator Kernel MVP

**Feature**: 001-orchestrator-kernel
**Format**: JSON Schema Draft 2020-12
**FR anchor**: FR-024 (Contract-First)
**Constitution anchor**: Article VII

本目录包含所有跨边界消息的机器可校验 schema。任何实现代码 MUST 只消费这里定义的 schema；
修改任一 schema 视同修改契约，MUST 触发 spec / plan 的 MINOR 版本变更。

## Schema inventory

| Schema | 边界 | FR anchors | Pydantic mirror |
|---|---|---|---|
| `entry-event.schema.json` | 外部入口 → 内核 | FR-001, FR-002, FR-031 | `src/orchestrator_kernel/contracts/entry_event.py` |
| `task.schema.json` | 内核内部 Task 树 | FR-004, FR-005, FR-018 | `contracts/task.py` |
| `budget.schema.json` | Task 预算（被 task / worker-registration 引用） | FR-018 | `contracts/budget.py` |
| `worker-registration.schema.json` | Worker → 内核（注册 + capability + 预算声明） | FR-007, FR-018, FR-025 | `contracts/worker.py` |
| `worker-protocol.schema.json` | 内核 ↔ Worker stdio JSON-lines 帧 | FR-008, FR-014, FR-016 | `contracts/worker_protocol.py` |
| `approval-message.schema.json` | 内核 ↔ 用户 审批请求与回写 | FR-010, FR-011, FR-012 | `contracts/approval.py` |
| `cancel-message.schema.json` | 用户 → 内核 取消指令 | FR-013, FR-014, FR-015 | `contracts/cancel.py` |
| `audit-event.schema.json` | 内核 → 文件系统 审计记录 | FR-019, FR-020, FR-021, FR-023 | `contracts/audit.py` |
| `result-summary.schema.json` | 内核 → 来源通道 终态回推 | FR-029, FR-030 | `contracts/result_summary.py` |

## Validation flow

```text
  external message  ─(JSON Schema validator)──▶  pydantic model  ──▶  kernel logic
                        raises on reject              strict typing

  kernel output     ─(pydantic.model_dump)──▶  json-lines  ──(JSON Schema validator)──▶  outbound
                        canonical form                  round-trip guard in tests/contract/
```

## Tests guarding these contracts

- `tests/contract/test_<schema>.py` — 每个 schema 一个测试文件
  - 正例：拿手写合法 payload 喂给 validator + pydantic，都通过
  - 反例：字段缺失 / 类型错 / 越界 / 枚举不符，**必须**被双端都拒绝
  - Round-trip：pydantic 导出 JSON → schema validator 过 → 再解析回 pydantic，字段等值

## 如何修改契约

1. 改 schema 文件
2. 跑 `pytest tests/contract/` 确认反例覆盖
3. 同步更新对应 pydantic 模块
4. 在 spec.md / plan.md / data-model.md 中追加变更说明
5. 触发 `/speckit-analyze` 重新审计一致性
