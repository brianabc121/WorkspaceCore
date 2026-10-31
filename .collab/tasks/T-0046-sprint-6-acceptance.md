---
id: T-0046
title: Sprint 6 验收（S6-04 ~ S6-06）：A15、备份恢复后的 Smoke Test、扫描与基准结果、验收报告
owner: claude
status: todo
priority: P1
branch:
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0042, T-0043, T-0044, T-0045
created: 2026-10-31
updated: 2026-10-31
---

# T-0046 Sprint 6 验收（S6-04 ~ S6-06）：A15、备份恢复后的 Smoke Test、扫描与基准结果、验收报告

## 目标
T-0042 ~ T-0045 合并后，按 Sprint 6 的 DoD 与 A15 做全栈验收，写验收报告，勾选 `docs/TASKS.md` 的 S6-04 ~ S6-06。
AI 部分（S6-01 ~ S6-03、A14 与 DoD 的前两条）等 T-0047 拆分后另行验收。

## 验收标准
- [ ] DoD“能从备份恢复并通过 Smoke Tests”实测通过，A15 在 CI 里自动运行；Sprint 1 ~ 5 的验收继续通过
- [ ] 安全扫描、性能基准、端到端 Smoke Test 的结果写进报告（通过 / 失败 / 未运行及原因）
- [ ] `docs/acceptance/sprint-6.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）；勾选 `docs/TASKS.md` 的 S6-04 ~ S6-06

## 背景 / 约束 / 参考
- 依赖：T-0042、T-0043、T-0044、T-0045
- 规范：第 13 节 Sprint 6，第 15 节 A15，第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
