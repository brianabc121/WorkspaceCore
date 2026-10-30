---
id: T-0020
title: Sprint 3 后端（一）：Integration 与凭证、Vikunja Connector、项目 / 任务读写、幂等与未知状态（S3-02 ~ S3-04 后端）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0019
created: 2026-10-30
updated: 2026-10-30
---

# T-0020 Sprint 3 后端（一）：Integration 与凭证、Vikunja Connector、项目 / 任务读写、幂等与未知状态（S3-02 ~ S3-04 后端）

## 目标
按 T-0019 的契约实现 Sprint 3 后端的第一部分：Vikunja 集成的配置与凭证、Connector 基础设施、项目 / 任务读写，
以及创建任务时的幂等与“结果未知”处理（规范 4.1：不能先声称成功，超时不盲目重建）。

## 验收标准
- [ ] S3-02：迁移 `integrations`、`integration_accounts`、`external_resources`、`idempotency_requests`（规范 5.1；空库 upgrade / downgrade 与 `alembic check`）；集成的增改查与测试连接，凭证用 T-0011 的加密服务保存（只写不回显）；`base_url` 做 SSRF 防护
- [ ] S3-02：Connector 基类（规范第 7 节的 Readable / Writable / WebhookCapable 分层与统一错误分类）；外部 HTTP 有超时、错误归类、有限重试与指数退避、熔断状态与 request_id；日志与审计不含 Token
- [ ] S3-03：项目列表与任务的列表 / 详情 / 创建 / 修改 / 删除经 Vikunja Connector；所有读写限定工作空间并校验角色；写成功后更新 `external_resources` 投影；首页聚合的 `tasks` 模块改为读本地投影（ok / stale / unauthorized）
- [ ] S3-04：`Idempotency-Key` 必填，请求指纹；同键重放最多创建一个外部任务（A06），同键不同请求 409（A07）；外部调用超时保留 unknown 状态，先查询匹配结果再决定，不盲目重建
- [ ] Connector 契约测试（规范第 12 节）：用 mock HTTP 覆盖成功、分页、401、403、429、5xx、断网、超时未知与 schema 变化；另有针对 T-0018 Compose 实例的集成测试（可在没有实例时跳过并注明）
- [ ] 写操作写入审计日志；`make lint`、`make test`、`make openapi` 通过；新增依赖写入 backend-stack ADR

## 背景 / 约束 / 参考
- 依赖：T-0019（契约）合并后开始
- 规范：第 4.1、4.2 节，第 5.1 节，第 6.3、6.4 节，第 7 节与 7.1，第 9 节（安全），第 12 节（测试）；验收场景 A05 ~ A07
- 通用约束：AGENTS.md 第 8 节第 3、4、6、7、8 条（核验第三方 API、迁移、脱敏、外部 HTTP、workspace scope）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
