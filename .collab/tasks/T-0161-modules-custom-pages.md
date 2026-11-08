---
id: T-0161
title: 插拔式模块（四）：侧栏自定义分组与零代码页面
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,tests/e2e
depends_on:
created: 2026-11-08
updated: 2026-11-08
---

# T-0161 插拔式模块（四）：侧栏自定义分组与零代码页面

## 目标
方案 P3：owner 在网页上新建侧栏分组、把页面挪到别的分组，添加零代码页面（外部链接、知识库笔记、仪表盘、嵌入页），
作为二级分类页出现在侧栏。

## 验收标准
- [x] 自定义分组与页面的表、迁移、接口和测试（owner 可改，其余成员按可见角色只读）
- [x] 侧栏按分组显示，零代码页面能打开；嵌入页只允许 https 且受 CSP 限制
- [x] 浏览器验收：owner 新建分组和页面，member 按角色看到；手机宽度不横向滚动

## 背景 / 约束 / 参考
方案：https://claude.ai/artifact/AqLi7VYg8UaQVmpWxfibmj（落库后见 `docs/v2/MODULES_PLAN.md`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 12:57 UTC · claude · 创建任务（owner: claude）
- 2026-11-08 14:04 UTC · claude · 开始任务
- 2026-11-08 14:10 UTC · claude · status in_progress → done：侧栏布局：迁移 0050、/api/v1/navigation、侧栏与页面设置页、/p/:id 嵌入与仪表盘页；EMBED_ORIGINS 与 CSP；本地全栈 modules_browser.cjs 通过
