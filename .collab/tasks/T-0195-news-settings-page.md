---
id: T-0195
title: AI 审批与管理区（B5）：新闻早报设置分组与生效状态
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/modules/news,apps/web/tests
depends_on: T-0189
created: 2026-11-09
updated: 2026-11-09
---

# T-0195 AI 审批与管理区（B5）：新闻早报设置分组与生效状态

## 目标
新闻早报设置按效果图 06 分组（基本与时间 / 来源 / 兴趣 / 模型与渠道 / 学习偏好）并显示生效状态。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B5）

## 验收标准
- [ ] owner 可写、admin 只读且不发写请求；模块停用不发设置请求
- [ ] “最近写入配置 #config_rev” 与 version 分开；render_error 不报全成功；未部署 / taken_by_other / online=null 如实显示
- [ ] dirty 时先保存再测试推送 / 现在生成；刷新只读；SSE / 刷新不抹 dirty
- [ ] 原有全部字段（两种方式、来源、主题、模型、渠道、学习偏好）都还能改；secret 只写；新测试与 news 回归通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 13:41 UTC · claude · B5 前端已在 b5-b5n 分支提交（2097f42）：设置页分五个标签共用一份草稿、管理区外壳、状态摘要条与右侧运行状态 / 推送渠道；新增 tests/news-settings-redesign.test.ts（19 条，先确认 17 条失败，模块停用两条本来就由宿主拦截）。lint / typecheck / 全量 vitest 665 条通过，build 通过，1440/1280/768/390 亮暗截图无横向滚动。下一步：主会话审查、合并进 B 系列 PR。
