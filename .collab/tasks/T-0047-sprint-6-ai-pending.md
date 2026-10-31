---
id: T-0047
title: Sprint 6 AI（S6-01 ~ S6-03）：等人类恢复 AI 厂商选型后再拆分
owner: human
status: todo
priority: P2
branch:
paths: docs/decisions
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0047 Sprint 6 AI（S6-01 ~ S6-03）：等人类恢复 AI 厂商选型后再拆分

## 目标
S6-01 ~ S6-03（AI provider 抽象、对话与消息、只读工具与检索、写工具审批与反 Prompt Injection）需要先选定第一版接入的模型接口。
2026-10-31 人类在对话中暂停了这个选型。本任务只是占位，球权在人类：人类恢复选型后，由 claude 拆成核验、契约、实现、验收几个任务。

## 验收标准
- [ ] 人类决定第一版接入的模型接口（规范要求接口抽象、不绑定单一厂商），以及是否提供真实密钥用于实测（只放在环境的 Secret 里，不在对话里提供）
- [ ] 决定后由 claude 拆分 S6-01 ~ S6-03 的任务（含核验 ADR、契约、实现、A14 与 DoD 的验收），本任务改为 done

## 背景 / 约束 / 参考
- 规范：第 1.2 节（AI 先只读工具，再开发显式审批的写入操作；不得向 LLM 暴露密钥），第 6.1 节 AI 接口，第 9.3 节，第 13 节 Sprint 6，第 15 节 A14，第 16 节
- 人类的暂停记录在 T-0040 的进度日志

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: human）
- 2026-10-31 08:57 UTC · claude · S6-04 ~ S6-06 已验收（docs/acceptance/sprint-6.md）。Sprint 6 只剩本任务的 AI 部分（S6-01 ~ S6-03、DoD 前两条、A14），等人类恢复 AI 服务选型后由 claude 拆分实施
- 2026-10-31 12:24 UTC · claude · 人类提供了 V2.0 设计资料包（docs/v2/），其中 AGENT_TASKS_V2 打算替换 S6-01 ~ S6-03；是否替换、应用内 AI 助手是否保留，见 docs/v2/REVIEW.md 第 2 节第 7 条和第 7 节
