---
id: T-0105
title: 时间轴前端：看板页面（电脑、手机）、任务页与项目页入口、按项目查看、任务详情的执行者与执行记录
owner: claude
status: todo
priority: P1
branch:
paths: apps/web,tests/e2e,docs
depends_on: T-0104
created: 2026-11-04
updated: 2026-11-04
---

# T-0105 时间轴前端：看板页面（电脑、手机）、任务页与项目页入口、按项目查看、任务详情的执行者与执行记录

## 目标
按效果图与方案第 2.1、2.6 节实现时间轴看板前端，以及任务页、项目页、任务详情的入口和执行者 / 执行记录。

## 验收标准
- [ ] `/tasks/timeline`：工具条、概况、刻度尺与播放头、执行者轨道与片段（计划、执行中、已完成、超时、等待审批、中断、人的任务）、token 轨道、全天概览与缩放、右侧详情；亮暗主题
- [ ] 按项目查看：项目下拉，地址带 `project`；任务页“显示方式”加“时间轴”；项目页加“时间轴”；任务详情“在时间轴中查看”
- [ ] 任务详情：执行者、计划开始、执行记录、开始 / 暂停 / 交给它执行 / 结束这段；viewer 只读
- [ ] 手机端：正在执行卡片、紧凑时间轴、底部面板；390 宽度不横向滚动
- [ ] 片段配色跑过 dataviz 配色检查；图例、悬停提示、键盘可操作、表格视图（不只靠颜色）
- [ ] apps/web lint / typecheck / test / build / check:api 通过；本机真实后端截图检查（1440 / 1024 / 390，亮暗）

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
