---
id: T-0059
title: OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）
owner: claude
status: todo
priority: P1
branch:
paths: docs/acceptance,docs/TASKS.md,docs/deployment.md,docs/threat-model.md,docs/data-dictionary.md,docs/architecture.md,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0056, T-0057, T-0058
created: 2026-10-31
updated: 2026-10-31
---

# T-0059 OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）

## 目标
Sprint 6 AI 部分的整体验收（方案第 8 节）。

## 验收标准
- [ ] `ci-infra` 新增多实例端到端任务：工作台加 OpenIM，实例 A 走内网、实例 B 走外网隧道，都用假模型；方案第 8 节 14 个场景全部自动化
- [ ] 文档：`docs/deployment.md`（服务器 A 的变更、OpenClaw 服务器检查清单、两种网络模式）、威胁模型、数据字典、架构
- [ ] A15 恢复演练覆盖 OpenIM；`docs/acceptance/sprint-6.md` 补充 AI 部分；勾选 `docs/TASKS.md` 的 S6-01 ~ S6-03

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
