---
id: T-0104
title: 时间轴后端：计划开始、执行者、执行记录、交给智能体执行、token 上报与归属、时间轴接口（迁移 0027）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs,scripts,tests/e2e
depends_on: T-0101
created: 2026-11-04
updated: 2026-11-04
---

# T-0104 时间轴后端：计划开始、执行者、执行记录、交给智能体执行、token 上报与归属、时间轴接口（迁移 0027）

## 目标
按方案第 2.2 ~ 2.5 节：任务有计划开始、执行者和执行记录；人的任务能开始 / 暂停，智能体的任务由管理员交给它执行（管理员的 IM 账号发消息），完成走原有审批；接入包每分钟上报 token，工作台按分钟算增量并归到执行段；提供时间轴接口。

## 验收标准
- [x] 迁移 0027：`tasks.planned_start_at` / `started_at` / 执行者，`task_runs`，`agent_usage_minutes`，`agent_usage_cursors`；可降级，`alembic check` 通过
- [x] 任务接口：执行者、计划开始；开始 / 结束一段；交给实例执行（`Idempotency-Key`，IM 结果不确定时不重复发）；执行记录接口
- [x] 完成任务时结束正在执行的段；实例停用 / 删除 / 离线超过 10 分钟时记为中断；不新增 MCP 写工具
- [x] `POST /agent-api/v1/usage`：实例凭证、限流、只收数字；按（实例、日期、会话）算增量，`null` 跳过，值变小以新值为准
- [x] 接入包 `bin/usage-report.mjs` + CHECKLIST；SSH 部署写入每分钟的 crontab 行；单元测试覆盖上报解析
- [x] `GET /api/v1/tasks/timeline`：轨道、片段（计划 / 执行段 / 等待审批 / 超时）、token 分桶（平均分时标近似）、概况；按项目筛选；工作空间与角色校验
- [x] api-contract、data-dictionary；后端 ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 03:57 UTC · claude · 开始任务
- 2026-11-04 04:01 UTC · claude · 实现完成：迁移 0027（tasks 四个字段、task_runs、agent_usage_minutes / cursors、agent_instances.usage_reported_at）；任务的执行者与计划开始；执行记录接口（开始 / 交给实例执行 / 结束 / 列表）；完成、改执行者、停用、离线 10 分钟结束或中断；POST /agent-api/v1/usage；GET /api/v1/tasks/timeline；接入包 bin/usage-report.mjs、CHECKLIST、SSH 部署的每分钟 crontab；文档。与方案的偏差写在 TIMELINE_PLAN 2.4.1：消息里请智能体用 complete_task（锁定版本 update_task 不能改完成状态）；只有在线的实例能交给它执行；sessions.usage 用 mode=utc、limit=1000（默认只返回 50 行，已在锁定镜像的参数定义里核验）；第一次上报只记基准；TaskCreate 不加新字段（它也是 create_task 工具的入参）。
- 2026-11-04 04:01 UTC · claude · 越界修改说明：契约变化（TaskOut 新字段、SSE 主题 timeline）必须同步前端才能保持 main 可用，所以改了 apps/web 的 schema.d.ts（生成）、features/events/useWorkspaceEvents.ts（新主题映射）、tests/sprint3-fixtures.ts（任务样例补字段）；没有其他前端改动，界面在 T-0105。另改 tests/e2e/agent_ssh_acceptance.py：SSH 部署的 crontab 从两行变三行。
