---
id: T-0186
title: 容器服务器（三）：改名、容器首屏与智能体用量
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/pages/RunnerPage.vue,apps/web/src/app,apps/web/src/features/runner,apps/web/src/features/agents/RunnerSwitch.vue,apps/web/tests
depends_on: T-0185
created: 2026-11-09
updated: 2026-11-09
---

# T-0186 容器服务器（三）：改名、容器首屏与智能体用量

## 目标
把“运行服务器”页改成“容器服务器”：首屏是容器总览（主机条、四个统计卡、筛选与服务端分页的容器表），新增“智能体用量”标签。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 3）

## 验收标准
- [x] 用户可见名称改为“容器服务器”；/runner、API、工具与模型名不变；旧 `?tab=environments` 替换到容器总览并保留筛选
- [x] 统计卡读 overview 全量（总数不含已删除、运行中、已停止、需关注 = 失败），表格显示服务端匹配总数与分页范围，每页 50
- [x] CPU 已用 / 上限、内存 MiB、数据卷占用按 DTO 显示；未知、过期、未运行、旧接口无 metrics 各自如实显示，不补 0
- [x] 智能体用量标签复用 RunnerInstanceUsage，标明“已分配 / 上限”，点击筛选该智能体的容器
- [x] `container-server-pages.test.ts`、`container-metrics.test.ts` 与既有 runner / navigation 测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 10:14 UTC · claude · 开始任务
- 2026-11-09 10:14 UTC · claude · 名称改为容器服务器（导航、页头、通知与提示文字；/runner 与接口不变）；主机条、统计卡（overview 全量）、容器总览（服务端搜索 / 筛选 / 排序 / 每页 50、匹配总数）、智能体用量；containerMetrics 统一读数口径；旧 ?tab=environments 替换到总览；container-metrics 8、container-server-pages 9 个测试；前端全量 66 文件 641 通过，lint / typecheck / build 通过；390–1440 亮暗截图自查无横向滚动
- 2026-11-09 10:14 UTC · claude · status in_progress → done：容器首屏与智能体用量完成
- 2026-11-09 10:20 UTC · claude · 越界说明：apps/web/src/features/agents/ServerInitializationPanel.vue（T-0181 的文件，#180 合并后在主干）里两处“运行服务器”的提示文字改成“容器服务器”，只改文案，不改逻辑
