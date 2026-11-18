---
id: T-0285
title: 工作台前端的内容区（id="main-content"）宽度改为 100%，去掉 1340px / 1880px 的上限
owner: claude
status: done
priority: P3
branch: claude/gracious-euler-ldkldx
paths: apps/web/src/style.css
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0285 工作台前端的内容区（id="main-content"）宽度改为 100%，去掉 1340px / 1880px 的上限

## 目标
人类 2026-11-17 在对话中要求：工作台前端 `id="main-content"` 这个内容区的宽度改为 100%。
编号在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6950688163

## 验收标准
- [x] `main` 占满侧栏右边的全部宽度。原来写着 `width: 100%`，但被 `max-width: 1340px` 限住；宽版页面（`main.is-wide`，任务、项目、日历、邮件、早报、大模型客户端、审计日志）是 1880px。现在两个上限都去掉。
- [x] 内边距不变，桌面 28px / 32px，手机 20px / 16px。
- [x] `is-wide` 仍由路由的 `meta.wide` 加上，只作标记，相关测试不受影响。

## 说明
- 只改 `apps/web/src/style.css`。各页面内部自己的宽度限制（例如说明文字最多 72 个字宽）不变。
- 很宽的屏幕上，表单、设置这类页面会被拉得比较宽，这是要求的效果。

## 进度日志
- 2026-11-17 claude：人类在对话中要求，编号在 #235 预留后完成。
- 2026-11-17 14:19 UTC · claude · status in_progress → done：去掉 main 的 1340px / 1880px 上限，内容区占满宽度
