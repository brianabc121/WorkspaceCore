---
id: T-0033
title: Sprint 5 版本核验：Prometheus / Grafana / n8n（锁定 release 与 digest、Prometheus HTTP API 只读查询、Grafana 外链、n8n Webhook 触发与执行记录 API、许可证）（S5-01、S5-04）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/deployment.md,infra/monitoring,infra/n8n
depends_on: T-0032
created: 2026-10-31
updated: 2026-10-31
---

# T-0033 Sprint 5 版本核验：Prometheus / Grafana / n8n（锁定 release 与 digest、Prometheus HTTP API 只读查询、Grafana 外链、n8n Webhook 触发与执行记录 API、许可证）（S5-01、S5-04）

## 目标
Sprint 5 接入 Prometheus / Grafana（可选监控）与 n8n（白名单触发）。项目规则禁止猜测第三方 API 与版本：先锁定版本，
核验实际要用到的接口与安全边界，写成 ADR，作为 T-0034 契约和 T-0035 / T-0037 实现的依据（参照 T-0018、T-0025 的做法）。

## 验收标准
- [x] Prometheus：锁定 release（镜像 tag + digest、许可证）；HTTP API 的即时 / 区间查询（`/api/v1/query`、`/api/v1/query_range`）的请求与响应格式、错误格式；只读、不暴露管理端（`/-/reload`、`/api/v1/admin/*` 等）的部署方式；数据保留（`--storage.tsdb.retention.time=7d` 等，S5-05）
- [x] V1 指标的来源与 PromQL（规范 7.4：请求次数、P50/P95/P99 延迟、5xx 率、服务 up/down、有 exporter 时的 CPU / 内存）：工作台自身如何暴露指标（例如 Prometheus 客户端库的版本与 `/metrics` 只在内部网络）；每个预设视图对应的固定查询，不接受浏览器传入的任意 PromQL
- [x] Grafana：锁定 release；工作台只做外链（规范 7.4），记录外链 URL 的结构与配置方式；可选 Compose 里的匿名访问、管理员密码与数据源预置方式
- [x] n8n：锁定 release 与许可证（Sustainable Use License，规范 7.5 要求商业托管前重新核验）；Webhook 触发节点的 URL 结构、认证方式（Header Auth 等）与响应；读取工作流与执行记录的 API（端点、API Key 认证、分页、字段）；不把 n8n 管理 Token 下发到浏览器
- [x] 可选 Compose：`infra/monitoring/`（Prometheus + Grafana）与 `infra/n8n/` 是否提供、如何加入工作台网络，不照搬依赖 trust 认证或浮动 `latest` 的官方模板（规范第 17 节、生产禁止浮动 latest）
- [x] 所有结论附来源链接（官方文档、源码 tag、运行实例实测）；无法核验的项标为未验证，后续以接口桩处理

## 背景 / 约束 / 参考
- 规范：第 7.4、7.5 节，第 11 节，第 13 节 Sprint 5，第 17 节（核验清单）
- 联网核验与 Docker 由 claude 负责（D-0003、D-0006）；不在对话里向人类索取凭据
- 参照 `docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`、`docs/decisions/2026-10-30-github-rest-and-affine-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 01:12 UTC · claude · 开始任务
- 2026-10-31 01:20 UTC · claude · 核验完成：docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md。锁定 Prometheus v3.15.1、Grafana 13.2.5、n8n 2.45.7（stable），均按 digest。实测：Prometheus 管理 / 生命周期接口默认关闭，但 /api/v1/status/config 可读，必须只在内部网络；查询 API 的成功 / 错误格式与错误码；保留 7d。Grafana 匿名关闭、禁止嵌入、只读数据源预置、/d/<uid>/<slug>。n8n 默认监听 :: 在无 IPv6 网络下退出（需 N8N_LISTEN_ADDRESS=0.0.0.0），readiness 才代表就绪；Webhook + Header Auth（错误密钥 403）；Public API 用限定 scope 的只读 API Key（写操作 403），执行记录默认不含节点数据。未核验并留给 T-0035 / T-0037：CPU / 内存 exporter、Grafana 时间参数的界面效果、触发与执行记录的对应、错误摘要的读取。文档网站被网络策略拒绝，改用各项目在对应 tag 下的源码与运行实例。
- 2026-10-31 01:20 UTC · claude · status in_progress → done：Prometheus / Grafana / n8n 版本核验 ADR 完成，T-0034 契约可以开始
