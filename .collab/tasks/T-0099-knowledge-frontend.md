---
id: T-0099
title: 知识库前端：独立页面、笔记与编辑器、知识图谱、变更记录、知识库主页，电脑端与移动端（K-2 + K-3）
owner: claude
status: todo
priority: P1
branch:
paths: apps/web,tests/e2e,docs
depends_on: T-0098
created: 2026-11-03
updated: 2026-11-03
---

# T-0099 知识库前端：独立页面、笔记与编辑器、知识图谱、变更记录、知识库主页，电脑端与移动端（K-2 + K-3）

## 目标
按效果图 https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv 实现知识库前端：
- 独立页面 `/knowledge`；
- 知识图谱（全局 / 局部）、笔记阅读、Markdown 编辑器（编辑 / 分屏 / 阅读、`[[` 补全、合并提示）；
- 变更记录（撤销、恢复）、项目与智能体知识库主页、回收站；
- 电脑端与移动端。

## 验收标准
- [ ] 路由与独立页面外壳（左侧功能条，移动端底部导航），书签移到“外部文档”
- [ ] 笔记阅读：属性、反向链接、局部图谱、版本历史；渲染消毒
- [ ] 编辑器：CodeMirror 6，三种模式、`[[` 与 `#` 补全、工具栏、草稿、版本冲突时三方合并
- [ ] 知识图谱：d3-force 布局，筛选、悬停高亮、搜索定位、列表视图，亮暗配色
- [ ] 变更记录与回收站：撤销、恢复、自动暂停与拦截记录
- [ ] 项目知识库主页（简报、参与的智能体、写入规则、最近变化）与智能体知识库主页
- [ ] apps/web lint / typecheck / test / build / check:api 通过；浏览器截图检查（1440 / 1024 / 390，亮暗）

## 背景 / 约束 / 参考
- 方案：`docs/v2/KNOWLEDGE_PLAN.md`（已批准）；核验：`docs/decisions/2026-11-03-knowledge-k0-verification.md`
- 效果图：https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 20:45 UTC · claude · 创建任务（owner: claude）
