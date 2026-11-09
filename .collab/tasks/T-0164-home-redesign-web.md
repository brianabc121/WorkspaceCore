---
id: T-0164
title: 首页改版（二）：按效果图重做总览页（工作概况、任务进展、我的待办、AI 新闻速览、提醒与日程、自定义首页）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/
depends_on: T-0163
created: 2026-11-08
updated: 2026-11-09
---

# T-0164 首页改版（二）：按效果图重做总览页（工作概况、任务进展、我的待办、AI 新闻速览、提醒与日程、自定义首页）

## 目标
按首页效果图重做“总览”页：页头（工作总览、问候语、更新时间、刷新、自定义首页）、工作概况五个数字、
任务进展（页签与任务表）、我的待办、AI 新闻速览、提醒与日程；任务详情里能确认智能体完成的结果。

## 验收标准
- [x] 新组件：工作概况、任务进展、我的待办；提醒与日程改为时间轴并可就地添加；新闻早报组件改为按主题的 AI 新闻速览
- [x] 页头与“自定义首页”（编辑模式里添加组件、保存布局；更多菜单“恢复默认首页”）
- [x] 任务详情“确认结果”、任务列表“待查看”标记；SSE 事件刷新首页数据
- [x] 亮 / 暗主题与手机宽度；lint / typecheck / vitest / build / check:api 通过

## 背景 / 约束 / 参考
方案 `docs/v2/HOME_PLAN.md`；效果图由人类在对话中给出（codex 制作）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 23:31 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 00:20 UTC · claude · 开始任务
- 2026-11-09 00:23 UTC · claude · status in_progress → done：前端完成：工作概况、任务进展、我的待办、AI 新闻速览、提醒与日程、自定义 / 恢复默认首页；lint/typecheck/test 507/build/check:api 通过
