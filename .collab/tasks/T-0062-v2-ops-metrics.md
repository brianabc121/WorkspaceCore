---
id: T-0062
title: V2 运维：运维页显示 OpenIM 与 OpenClaw 实例状态、Prometheus 指标（方案第 2、6.1 节）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/deployment.md,scripts/security-scan.sh,services/api/app/domain/ops_metrics.py,services/api/app/domain/operations.py,services/api/app/schemas/operations.py,services/api/app/main.py,services/api/app/domain/agents.py,services/api/app/domain/mcp_tools.py,services/api/app/domain/im_callbacks.py,services/api/app/jobs/worker.py,services/api/tests,services/api/openapi.json,apps/web/src/features/operations,apps/web/src/services/schema.d.ts,apps/web/tests/operations-pages.test.ts,infra/monitoring/grafana,docs/api-contract.md
depends_on: T-0057
created: 2026-11-01
updated: 2026-11-01
---

# T-0062 V2 运维：运维页显示 OpenIM 与 OpenClaw 实例状态、Prometheus 指标（方案第 2、6.1 节）

## 目标
方案（`docs/v2/TRANSFORMATION_PLAN.md`）第 2 节“运维页增加 OpenClaw 实例和 OpenIM 的状态”与第 6.1 节的监控项
（实例在线数、取密钥成功与失败次数、MCP 调用量、回调积压量、对账补齐条数、防刷屏暂停次数、待审批数）
在 OC-2 ~ OC-6 里都没有实现，T-0059 验收时发现。方案已获人类批准、由 claude 实施（D-0007），这里补上。

## 验收标准
- [x] 运维页服务列表新增 `kind = agents` 一组：`openim`（没部署为 not_configured；不可用为 down；回调收件箱有等待超过 5 分钟的事件时 degraded）
      与本工作空间的 OpenClaw 实例（状态来自实例表；只对 owner / admin 列出）
- [x] api 的 `/metrics` 输出方案第 6.1 节的各项指标；标签只用固定枚举值，不含实例 ID、工作空间 ID、Token、用户输入；数据库或 Redis 不可用时只返回请求指标
- [x] Grafana 预置仪表盘增加对应面板，测试保证仪表盘查询的指标都存在
- [x] 前端运维页显示新分组与原因文字；OpenAPI、`schema.d.ts`、`docs/api-contract.md`、`docs/deployment.md` 第 12.2 节同步
- [x] 方案第 7 节“镜像扫描覆盖 OpenIM 和 OpenClaw”：`scripts/security-scan.sh images` 的第三方清单加入 agents profile、OpenIM 的依赖镜像、OpenClaw 与 ghostunnel（补丁镜像已由 ci-openim 扫描）
- [x] 后端 ruff / mypy / pytest、前端 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
- 计数类记在 Redis 哈希（api 多进程与 Worker 都会写），状态类抓取时查 PostgreSQL
- `docs/deployment.md` 同时在 T-0059 的范围里（同为 claude 的任务），这里只改第 12.2 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 13:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 13:15 UTC · claude · 开始任务：方案第 2、6.1 节已获人类批准、由 claude 实施（D-0007）；T-0059 验收时发现未实现
- 2026-11-01 13:35 UTC · claude · status in_progress → done：运维页“OpenClaw 与 IM”一组（openim + 本工作空间实例，只对 owner / admin）、/metrics 的 V2 指标（实例、取密钥、实例认证失败、MCP、待审批、IM 回调积压、对账补齐、防刷屏暂停）、Grafana 四个面板、前端分组与原因、镜像扫描覆盖 OpenIM 依赖 / agent-gateway / OpenClaw / ghostunnel。后端 569 passed / 14 skipped，test_ops_v2 3 项（含仪表盘指标名核对）；前端 304 项、lint、typecheck、build、check:api 通过
