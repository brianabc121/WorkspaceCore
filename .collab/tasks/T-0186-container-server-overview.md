---
id: T-0186
title: 容器服务器（三）：改名、容器首屏与智能体用量
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/RunnerPage.vue,apps/web/src/app,apps/web/src/features/runner,apps/web/src/features/agents/RunnerSwitch.vue,apps/web/tests
depends_on: T-0185
created: 2026-11-09
updated: 2026-11-09
---

# T-0186 容器服务器（三）：改名、容器首屏与智能体用量

## 目标
把“运行服务器”页改成“容器服务器”：首屏是容器总览（主机条、四个统计卡、筛选与服务端分页的容器表），新增“智能体用量”标签。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 3）

## 验收标准
- [ ] 用户可见名称改为“容器服务器”；/runner、API、工具与模型名不变；旧 `?tab=environments` 替换到容器总览并保留筛选
- [ ] 统计卡读 overview 全量（总数不含已删除、运行中、已停止、需关注 = 失败），表格显示服务端匹配总数与分页范围，每页 50
- [ ] CPU 已用 / 上限、内存 MiB、数据卷占用按 DTO 显示；未知、过期、未运行、旧接口无 metrics 各自如实显示，不补 0
- [ ] 智能体用量标签复用 RunnerInstanceUsage，标明“已分配 / 上限”，点击筛选该智能体的容器
- [ ] `container-server-pages.test.ts`、`container-metrics.test.ts` 与既有 runner / navigation 测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
