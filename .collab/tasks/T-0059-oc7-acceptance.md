---
id: T-0059
title: OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,docs/deployment.md,docs/threat-model.md,docs/data-dictionary.md,docs/architecture.md,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0056, T-0057, T-0058
created: 2026-10-31
updated: 2026-11-01
---

# T-0059 OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）

## 目标
Sprint 6 AI 部分的整体验收（方案第 8 节）。

## 验收标准
- [ ] `ci-infra` 新增多实例端到端任务：工作台加 OpenIM，实例 A 走内网、实例 B 走外网隧道，都用假模型；方案第 8 节 14 个场景全部自动化
- [ ] 文档：`docs/deployment.md`（服务器 A 的变更、OpenClaw 服务器检查清单、两种网络模式）、威胁模型、数据字典、架构
- [ ] A15 恢复演练覆盖 OpenIM；`docs/acceptance/sprint-6.md` 补充 AI 部分；勾选 `docs/TASKS.md` 的 S6-01 ~ S6-03
- [ ] ci-infra 运行 `tests/e2e/agent_ingress_acceptance.py`（接入入口、双向证书、本机隧道；测试证书在 CI 里生成）（OC-2 留下）

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 11:53 UTC · claude · 开始任务
- 2026-11-01 11:54 UTC · claude · 第 1 批：ci-infra 新增 agents job（与 compose job 并行）：生成 .env 与两张自签名测试证书（intranet.test / public.test，只在 CI 里用），make up → make migrate → make agents-up（导出内部 CA、启动 agent-gateway），然后运行 tests/e2e/agent_ingress_acceptance.py（13 项：内网/外网入口、双向证书、本机隧道、/mcp initialize、入口错配、吊销、伪造请求头）。本机在 b7e2e 栈上用同样参数跑过 13 项全部通过（T-0058）。验收标准第 4 项待 CI 通过后勾选。下一批：多实例端到端（OpenIM + 两个真实 OpenClaw 实例 + 假模型，方案第 8 节 14 个场景），然后文档、A15 覆盖 OpenIM、验收报告
