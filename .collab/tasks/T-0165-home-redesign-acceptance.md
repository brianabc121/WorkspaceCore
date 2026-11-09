---
id: T-0165
title: 首页改版（三）：浏览器验收、文档与截图对照
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/,.github/workflows/,docs/
depends_on: T-0163, T-0164
created: 2026-11-08
updated: 2026-11-09
---

# T-0165 首页改版（三）：浏览器验收、文档与截图对照

## 目标
首页改版的浏览器验收、受影响的 Sprint 2 / 3 验收脚本、性能基准与文档。

## 验收标准
- [x] `tests/e2e/home_browser.cjs`：四种状态、需要关注、页签、确认结果、我的待办添加与完成、添加提醒、恢复默认首页、viewer 只读、手机宽度；接入 ci-infra compose job
- [x] Sprint 2 / 3 浏览器与接口验收改用新的默认首页；性能基准加任务进展与我的待办
- [x] 本地全栈：home / sprint2 / sprint3 / modules 浏览器验收与 sprint2 接口验收通过；截图与效果图对照
- [x] api-contract、data-dictionary、threat-model 更新

## 背景 / 约束 / 参考
方案 `docs/v2/HOME_PLAN.md`；效果图由人类在对话中给出（codex 制作）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 23:31 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 00:20 UTC · claude · 开始任务
- 2026-11-09 00:23 UTC · claude · status in_progress → done：验收完成：home_browser 接入 ci-infra；sprint2/sprint3/modules 浏览器与 sprint2 接口验收、性能基准本地通过；截图与效果图对照
