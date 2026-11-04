---
id: T-0104
title: 时间轴后端：计划开始、执行者、执行记录、交给智能体执行、token 上报与归属、时间轴接口（迁移 0027）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,docs,scripts,tests/e2e
depends_on: T-0101
created: 2026-11-04
updated: 2026-11-04
---

# T-0104 时间轴后端：计划开始、执行者、执行记录、交给智能体执行、token 上报与归属、时间轴接口（迁移 0027）

## 目标
按方案第 2.2 ~ 2.5 节：任务有计划开始、执行者和执行记录；人的任务能开始 / 暂停，智能体的任务由管理员交给它执行（管理员的 IM 账号发消息），完成走原有审批；接入包每分钟上报 token，工作台按分钟算增量并归到执行段；提供时间轴接口。

## 验收标准
- [ ] 迁移 0027：`tasks.planned_start_at` / `started_at` / 执行者，`task_runs`，`agent_usage_minutes`，`agent_usage_cursors`；可降级，`alembic check` 通过
- [ ] 任务接口：执行者、计划开始；开始 / 结束一段；交给实例执行（`Idempotency-Key`，IM 结果不确定时不重复发）；执行记录接口
- [ ] 完成任务时结束正在执行的段；实例停用 / 删除 / 离线超过 10 分钟时记为中断；不新增 MCP 写工具
- [ ] `POST /agent-api/v1/usage`：实例凭证、限流、只收数字；按（实例、日期、会话）算增量，`null` 跳过，值变小以新值为准
- [ ] 接入包 `bin/usage-report.mjs` + CHECKLIST；SSH 部署写入每分钟的 crontab 行；单元测试覆盖上报解析
- [ ] `GET /api/v1/tasks/timeline`：轨道、片段（计划 / 执行段 / 等待审批 / 超时）、token 分桶（平均分时标近似）、概况；按项目筛选；工作空间与角色校验
- [ ] api-contract、data-dictionary；后端 ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
