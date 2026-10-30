---
id: T-0023
title: Sprint 3 集成与验收：DoD 实测、A06 ~ A09 自动化、真实 Vikunja 的全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0020, T-0021, T-0022
created: 2026-10-30
updated: 2026-10-30
---

# T-0023 Sprint 3 集成与验收：DoD 实测、A06 ~ A09 自动化、真实 Vikunja 的全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0020、T-0021、T-0022 合并后做前后端与真实 Vikunja 的联调，并按 Sprint 3 的 DoD 实测验收。
通过后勾选 `docs/TASKS.md` 的 S3-01 ~ S3-06，请人类批准进入 Sprint 4。

## 验收标准
- [x] DoD 实测（规范第 13 节）：在 Vikunja 原生界面修改任务，最终出现在工作台；重复提交不创建双份任务；模拟 webhook 丢失后经对账补齐
- [x] 在 Compose 全栈 + T-0018 的 Vikunja 实例上自动化 A06（同一幂等键重放最多创建一个外部任务）、A07（同键不同请求 409）、A08（重复送达最多一次有效副作用）、A09（webhook 丢失后对账纠正）并纳入 CI；A05 以停掉 Vikunja 后首页任务模块 stale / unavailable、其余组件可用验证
- [x] 浏览器 E2E 纳入 CI：连接 Vikunja → 新建 / 编辑任务 → 在 Vikunja 原生修改 → 工作台显示更新
- [x] `docs/acceptance/sprint-3.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [x] 勾选 `docs/TASKS.md` 的 S3-01 ~ S3-06；escalate 给人类，请求批准进入 Sprint 4

## 背景 / 约束 / 参考
- 依赖：T-0020、T-0021、T-0022 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 13 节（Sprint 3 DoD）、第 15 节（A05 ~ A09）、第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 15:59 UTC · claude · 人类在对话中同意：T-0022 合并前先做不依赖前端的部分（真实 Vikunja 的全栈接口级验收 A05 ~ A09、DoD 实测、纳入 CI）；浏览器 E2E、验收报告定稿与勾选 TASKS.md 等 T-0022 合并后再做。
- 2026-10-30 15:59 UTC · claude · 开始任务
- 2026-10-30 16:13 UTC · claude · 第一部分完成（接口级，人类同意在 T-0022 之前先做）：tests/e2e/sprint3_acceptance.py（29 项，真实 Vikunja 2.7.1）覆盖 DoD 三条与 A05 ~ A09，并纳入 ci-infra（make vikunja-up → 验收 → make vikunja-down；CI 中 RECONCILE_INTERVAL_MINUTES=1 让 A09 走真正的周期对账）。本机按 CI 顺序跑通 Sprint 1 / 2 / 3 接口级验收和重启检查。验收中发现并修复：Vikunja 断开时首页任务模块在 15 分钟内仍显示 ok（与 A05 不符），改为集成 unavailable 时 stale（source_unavailable）；对账间隔改为可配置（RECONCILE_INTERVAL_MINUTES，默认 5）。越界说明：services/api（connectors.py、config.py、worker.py、test_tasks_api.py）、compose.yaml（worker 传入 RECONCILE_INTERVAL_MINUTES）、契约 ADR 第 10 / 10.1 节、部署文档，均为 claude 所有、无对方进行中任务占用。验收报告 docs/acceptance/sprint-3.md 为进行中稿。下一步（T-0022 合并后）：浏览器 E2E（连接 Vikunja → 新建 / 编辑 → 原生修改 → 页面更新）纳入 CI，报告定稿，勾选 TASKS.md S3-01 ~ S3-06，escalate 请求进入 Sprint 4。
- 2026-10-30 16:37 UTC · claude · 第二部分完成：T-0022（PR #33，codex）审查后合并；tests/e2e/sprint3_browser.cjs（10 步，真实 API + Vikunja 2.7.1：连接 → 测试 → 列表 → 新建（幂等键）→ 编辑 → 原生修改后页面更新 → 看板 → 首页汇总 → 删除 → 移动端 → viewer 只读）纳入 ci-infra，Vikunja 停止步骤移到浏览器验收之后。本机按 CI 顺序从空库完整跑通（Sprint 1/2/3 接口级、Sprint 2/3 浏览器、重启检查、Sprint 1 浏览器），Sprint 3 浏览器连续 3 次通过。验收报告 docs/acceptance/sprint-3.md 定稿；docs/TASKS.md 勾选 S3-01 ~ S3-06（D-0004 第 3 节：验收任务勾选不算例外）。按 D-0004 进入 Sprint 4 需人类批准，escalate。
- 2026-10-30 16:37 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-30-1637_T-0023_claude-to-human.md：Sprint 3 验收通过，请批准是否进入 Sprint 4
- 2026-10-30 16:43 UTC · human · owner human → claude：人类在对话中批准进入 Sprint 4（对“同意的话回复‘批准进入 Sprint 4’”回复“同意”）；未对 PR #33 的 3 条非阻塞建议另作指示；指派 claude 关闭验收任务
- 2026-10-30 16:43 UTC · claude · 解除阻塞，继续
- 2026-10-30 16:43 UTC · claude · status in_progress → done：Sprint 3 验收通过，人类已批准进入 Sprint 4
