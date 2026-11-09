---
id: T-0185
title: 容器服务器（二）：指标接收与新鲜度、单环境详情、服务端分页搜索与迁移
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/models/runner.py,services/api/app/schemas/runner.py,services/api/app/domain/runner_hosts.py,services/api/app/domain/runner_admin.py,services/api/app/domain/runner_metrics.py,services/api/app/api/v1/runner.py,services/api/migrations/versions,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts
depends_on: T-0184
created: 2026-11-09
updated: 2026-11-09
---

# T-0185 容器服务器（二）：指标接收与新鲜度、单环境详情、服务端分页搜索与迁移

## 目标
服务端保存每个环境最新的 CPU / 内存 / 数据卷读数与平台接收时间，按 180 秒判断新鲜度，提供单环境详情与完整范围的服务端分页搜索。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 2）

## 验收标准
- [x] 迁移只加 nullable 列：cpu_used_cores、cpu_window_seconds、cpu_reported_at、mem_reported_at、disk_reported_at；旧记录不回填；upgrade / downgrade / upgrade 在临时库通过
- [x] report 在原事务里写读数与服务端 UTC 时间，不改 last_used_at / version；失败项清空，普通 poll 不刷新指标时间；不能写别的主机的环境
- [x] `RunnerEnvMetrics` 与 `environment_metrics()` 纯函数：百分比口径、180 秒边界、主机离线 / 停用为 stale、非 running 为 not_running、归档 / 删除后数据卷 unavailable
- [x] `GET /runner/environments/{id}`（admin，跨空间 404）；列表加 q / sort / cursor / limit、total、next_cursor，501 条记录分页无重漏、最后一条可搜到、换空间游标 422
- [x] runner_api / runner_reclaim 回归通过；OpenAPI 与 schema.d.ts 重新生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 09:50 UTC · claude · 开始任务
- 2026-11-09 09:50 UTC · claude · 迁移 0055、runner_metrics（读数写入、新鲜度纯函数、排序键）、详情接口、列表 q/sort/cursor/limit/total/next_cursor、通知文案改“容器服务器”；test_runner_metrics 20、test_runner_metrics_api 9、迁移 1 与 runner 回归共 81 passed；数据卷读不到时保留上次读数（回收保护需要，ADR 记偏差）
- 2026-11-09 09:50 UTC · claude · status in_progress → done：指标存储、新鲜度、详情与分页接口完成，测试通过
