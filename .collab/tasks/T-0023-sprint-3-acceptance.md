---
id: T-0023
title: Sprint 3 集成与验收：DoD 实测、A06 ~ A09 自动化、真实 Vikunja 的全栈与浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: todo
priority: P1
branch:
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
- [ ] DoD 实测（规范第 13 节）：在 Vikunja 原生界面修改任务，最终出现在工作台；重复提交不创建双份任务；模拟 webhook 丢失后经对账补齐
- [ ] 在 Compose 全栈 + T-0018 的 Vikunja 实例上自动化 A06（同一幂等键重放最多创建一个外部任务）、A07（同键不同请求 409）、A08（重复送达最多一次有效副作用）、A09（webhook 丢失后对账纠正）并纳入 CI；A05 以停掉 Vikunja 后首页任务模块 stale / unavailable、其余组件可用验证
- [ ] 浏览器 E2E 纳入 CI：连接 Vikunja → 新建 / 编辑任务 → 在 Vikunja 原生修改 → 工作台显示更新
- [ ] `docs/acceptance/sprint-3.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [ ] 勾选 `docs/TASKS.md` 的 S3-01 ~ S3-06；escalate 给人类，请求批准进入 Sprint 4

## 背景 / 约束 / 参考
- 依赖：T-0020、T-0021、T-0022 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 13 节（Sprint 3 DoD）、第 15 节（A05 ~ A09）、第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
