---
id: T-0173
title: 二批四页（二）：知识库沉浸式总览（空间导航、真实统计、局部关联、选中笔记、最近更新）与完整图谱路由
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/kb,apps/web/src/pages/KnowledgePage.vue,apps/web/src/app/router.ts,apps/web/tests,tests/e2e
depends_on: T-0172
created: 2026-11-09
updated: 2026-11-09
---

# T-0173 二批四页（二）：知识库沉浸式总览（空间导航、真实统计、局部关联、选中笔记、最近更新）与完整图谱路由

## 目标
知识库落到沉浸式总览：空间导航、四项真实统计、局部关联图、笔记阅读卡和最近更新；完整图谱移到 `/knowledge/graph`。
方案见 `docs/v2/pages-redesign-2/`（Task 2）。

## 验收标准
- [ ] `/knowledge` 是总览，`/knowledge/graph` 是完整图谱；旧 `/knowledge?focus=<id>` 转到完整图谱并保留其他参数
- [ ] 统计数字只来自服务端 summary，不用图谱节点数；未知显示未知，空空间是真正的空态
- [ ] 选中笔记按 ID 取详情，同名不同 ID 不串；404 / 失权清空；切换工作空间后迟到的旧响应被丢弃
- [ ] 局部图谱卸载时停止 simulation、移除监听，并有列表替代
- [ ] 编辑、版本、冲突、回收站、README、AFFiNE 入口仍可达；viewer 只读
- [ ] `kb-overview.test.ts` 与既有知识库测试通过；亮 / 暗、桌面 / 手机截图与浏览器验收通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
