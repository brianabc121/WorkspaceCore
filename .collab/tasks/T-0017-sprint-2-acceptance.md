---
id: T-0017
title: Sprint 2 集成与验收：DoD 实测、A03/A04 自动化、浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: todo
priority: P1
branch:
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0015, T-0016
created: 2026-10-30
updated: 2026-10-30
---

# T-0017 Sprint 2 集成与验收：DoD 实测、A03/A04 自动化、浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0015、T-0016 合并后做前后端联调，并按 Sprint 2 的 DoD 实测验收。通过后勾选 `docs/TASKS.md` 的 S2-01 ~ S2-05，请人类批准进入 Sprint 3。

## 验收标准
- [ ] DoD 实测（规范第 13 节）：拖拽后刷新布局不丢；未知 widget type 安全报错；部分 Widget 失败不影响整页
- [ ] 在 Compose 全栈环境下自动化 A03（viewer 修改 Widget 返回 403）、A04（保存并刷新首页布局一致，版本号更新）并纳入 CI；A05 在尚无 Connector 的情况下以模块 unavailable / stale 的降级展示验证
- [ ] 浏览器 E2E（S2-05）纳入 CI：首页加载 → 拖拽保存 → 刷新后布局一致；同时把 Sprint 1 的浏览器流程纳入（`tests/e2e` 已有锁定版本的 Playwright）
- [ ] `docs/acceptance/sprint-2.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [ ] 勾选 `docs/TASKS.md` 的 S2-01 ~ S2-05；escalate 给人类，请求批准进入 Sprint 3

## 背景 / 约束 / 参考
- 依赖：T-0015、T-0016 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 13 节（Sprint 2 DoD）、第 16 节（A03、A04、A05）、第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: claude）
