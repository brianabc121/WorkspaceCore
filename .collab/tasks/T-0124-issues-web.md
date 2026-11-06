---
id: T-0124
title: Issues 页面：项目页 Issues 标签、任务抽屉讨论标签、通知、PR 状态
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0123
created: 2026-11-04
updated: 2026-11-06
---

# T-0124 Issues 页面：项目页 Issues 标签、任务抽屉讨论标签、通知、PR 状态

## 目标
在网页上使用 Issues（方案第 8.8 节）。

## 验收标准
- [x] 项目页 Issues 标签：列表、筛选、详情、模板新建、评论、关闭
- [x] 任务抽屉讨论标签；通知；PR 状态显示；项目群提示开关
- [x] 前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 10:15 UTC · claude · 开始任务
- 2026-11-06 10:15 UTC · claude · 页面完成：项目页加“Issues”标签（?tab=issues；未关闭 / 已关闭与数量、按类型 / 标签 / 指派给我 / 提到我 / 关键词筛选、未读标记、新建先选类型再套模板）；Issue 详情页 /issues/:id 与 /projects/:project/issues/:number（正文与评论用知识库的安全 Markdown 渲染，#编号 链到同一项目、@名字 标成提及，代码里的不算；评论与事件按时间合在一条时间线里；侧栏指派、标签、关联任务、PR 状态、参与者；关闭要选原因、重复要写编号；管理员锁定、隐藏评论、删除；打开时记为已读）；任务抽屉加“讨论”标签（关联的 Issue、阻塞提示、在这里新建自动关联任务）；管理员的项目群提示开关；通知与搜索链接到 /issues/:id；SSE 主题 issues 刷新。后端补了 GET /projects/{id}/issues/{number} 与 ProjectOut.issue_notices。apps/web/tests/issues-pages.test.ts 4 个。
- 2026-11-06 10:50 UTC · claude · status in_progress → done：web lint / typecheck / 49 个文件 453 个测试 / build 通过；issues-pages.test.ts 4 个覆盖列表筛选、模板新建、安全渲染与时间线、管理员操作、viewer 只读、任务抽屉讨论。浏览器验收随 T-0125。
