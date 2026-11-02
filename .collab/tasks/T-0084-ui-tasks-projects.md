---
id: T-0084
title: UI 优化第 2 批：任务与项目（智能视图、快速添加、勾选完成、看板拖动、项目进度）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web,tests/e2e,docs
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0084 UI 优化第 2 批：任务与项目（智能视图、快速添加、勾选完成、看板拖动、项目进度）

## 目标
人类要求（2026-11-02）：「给我重新优化一下整个工作台的 UI 以及功能」。第 2 批：任务与项目页的日常操作更顺手。
审查发现：任务行很松（约 120px），完成一个任务要打开抽屉、勾选、保存；没有“今天 / 逾期”视图；筛选面板 6 个字段常驻；看板不能拖；项目页看不出进度。

## 验收标准
- [x] 智能视图：全部 / 未完成 / 今天 / 已逾期 / 未来 7 天 / 已完成，一键切换（转成接口已有的 status、due_after、due_before 参数）
- [x] 快速添加：标题 + 项目，回车保存；“今天”视图里添加的任务截止今天；走原来的任务写入（Idempotency-Key、结果未知时的查询）
- [x] 列表里勾选完成 / 取消完成（带 version）；成功后列表即时更新；viewer 没有勾选框和快速添加
- [x] 紧凑任务行：勾选框、标题、项目与标签、优先级、截止时间（逾期、今天高亮）
- [x] 更多筛选收进可展开区域；增加排序（截止时间 / 优先级 / 最近更新）
- [x] 看板：把卡片拖到另一列改变完成状态（勾选框是键盘可用的等价操作）
- [x] 项目页：每个项目显示完成进度条
- [x] 前端 lint / typecheck / test / build / check:api 通过；浏览器截图验收；受影响的 e2e 脚本同步更新

## 背景 / 约束 / 参考
- 只改前端，不改接口与数据表（项目颜色需要新字段，不在本批）。
- 任务写入仍然一次只进行一个操作（`stores/taskOperations.ts`），结果未知时按原来的方式查询，不重复提交。
- 依赖 T-0083 的外框与设计令牌。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 23:30 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-02 23:30 UTC · human · owner unassigned → claude：人类在对话中指派：给我重新优化一下整个工作台的 UI 以及功能（第 2 批：任务与项目）
- 2026-11-02 23:30 UTC · claude · 开始任务
- 2026-11-02 23:40 UTC · claude · 完成：智能视图（viewQuery，按浏览器时区）、排序、更多筛选可展开；快速添加与列表 / 看板勾选完成都走 taskOperations（Idempotency-Key、结果未知查询），成功后写回新版本到列表缓存；看板 HTML5 拖放；任务行紧凑化（逾期 / 今天高亮）；项目卡片网格 + 进度条。新增 tests/task-quick-actions.test.ts（12 项）；浏览器里真实勾选、回车快速添加、dragTo 拖放通过。ADR 追加第 4 节。
- 2026-11-02 23:40 UTC · claude · status in_progress → done：UI 第 2 批（任务与项目）完成：web lint / typecheck / test 357 / build / check:api 通过；browser-tasks.cjs 与真实浏览器勾选、快速添加、拖放检查通过
