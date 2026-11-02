---
id: T-0071
title: Web 测试：日历写入恢复测试固定显示的那一周（不依赖当前日期）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/tests/resource-mutation.test.ts
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0071 Web 测试：日历写入恢复测试固定显示的那一周（不依赖当前日期）

## 目标
2026-11-02（周一，UTC）起 `apps/web/tests/resource-mutation.test.ts` 的 5 个日历测试失败（PR #112 的 web 检查）：
日历页默认显示“本周”，测试数据的日程在 2026-10-30（上一周），页面上找不到日程。测试依赖当前日期，main 上同样会失败。

## 验收标准
- [x] 这些测试用 `/calendar?date=2026-10-30` 打开日历（与 `global-search.test.ts` 相同做法），不再依赖当前日期
- [x] `npx vitest run` 全部通过；lint、typecheck 通过

## 背景 / 约束 / 参考
- 失败记录：PR #112 web job 125058274917（5 failed，`Unable to find role="button" and name "发布评审"`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 02:20 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 02:20 UTC · claude · 开始任务
- 2026-11-02 02:20 UTC · claude · 根因：测试依赖当前周；改为 ?date=2026-10-30；本地 vitest 33 文件 328 个全部通过
- 2026-11-02 02:20 UTC · claude · status in_progress → done：日历测试固定日期，全部通过
