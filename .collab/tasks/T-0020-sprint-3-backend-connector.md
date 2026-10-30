---
id: T-0020
title: Sprint 3 后端（一）：Integration 与凭证、Vikunja Connector、项目 / 任务读写、幂等与未知状态（S3-02 ~ S3-04 后端）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
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
- [x] S3-02：迁移 `integrations`、`integration_accounts`、`external_resources`、`idempotency_requests`（规范 5.1；空库 upgrade / downgrade 与 `alembic check`）；集成的增改查与测试连接，凭证用 T-0011 的加密服务保存（只写不回显）；`base_url` 做 SSRF 防护
- [x] S3-02：Connector 基类（规范第 7 节的 Readable / Writable / WebhookCapable 分层与统一错误分类）；外部 HTTP 有超时、错误归类、有限重试与指数退避、熔断状态与 request_id；日志与审计不含 Token
- [x] S3-03：项目列表与任务的列表 / 详情 / 创建 / 修改 / 删除经 Vikunja Connector；所有读写限定工作空间并校验角色；写成功后更新 `external_resources` 投影；首页聚合的 `tasks` 模块改为读本地投影（ok / stale / unauthorized）
- [x] S3-04：`Idempotency-Key` 必填，请求指纹；同键重放最多创建一个外部任务（A06），同键不同请求 409（A07）；外部调用超时保留 unknown 状态，先查询匹配结果再决定，不盲目重建
- [x] Connector 契约测试（规范第 12 节）：用 mock HTTP 覆盖成功、分页、401、403、429、5xx、断网、超时未知与 schema 变化；另有针对 T-0018 Compose 实例的集成测试（可在没有实例时跳过并注明）
- [x] 写操作写入审计日志；`make lint`、`make test`、`make openapi` 通过；新增依赖写入 backend-stack ADR

## 背景 / 约束 / 参考
- 依赖：T-0019（契约）合并后开始
- 规范：第 4.1、4.2 节，第 5.1 节，第 6.3、6.4 节，第 7 节与 7.1，第 9 节（安全），第 12 节（测试）；验收场景 A05 ~ A07
- 通用约束：AGENTS.md 第 8 节第 3、4、6、7、8 条（核验第三方 API、迁移、脱敏、外部 HTTP、workspace scope）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 14:32 UTC · claude · 开始任务
- 2026-10-30 15:14 UTC · claude · 完成后端（一）。实现：迁移 0005（integrations、integration_accounts、external_resources、idempotency_requests、sync_cursors）；Connector 能力分层（app/integrations/base.py：Connector / Readable / Writable / WebhookCapable + 统一错误分类）；出站 HTTP（SSRF 允许列表、超时、只对读重试、熔断、X-Request-ID、不跟随重定向）；Vikunja v2 客户端；集成增改查与测试连接（Token 加密、只写不读，测试通过后全量同步）；项目 / 任务读投影、写经 Connector；Idempotency-Key 全部状态（A06 / A07 / in progress / unknown 查询 / not applied / not sent 可重试）；首页 tasks 模块读投影（ok / stale / unauthorized）。真实实例发现：Vikunja 写入响应的时间带纳秒、读取只到秒，version 改为 updated 截到秒的 UTC（核验 ADR 新增 5.5 节，契约 6.3 / 7 节补充）。测试：192 个通过（含 tests/test_vikunja_live.py 对真实 2.7.1 实例 4 个，设置 TEST_VIKUNJA_URL / TOKEN 时运行，CI 跳过）。越界说明：compose.yaml 与 .env.example 新增 INTEGRATION_ALLOWED_HOSTS（默认 vikunja），docs/ 下的 ADR、数据字典、部署与接口文档，按 D-0005 重新生成 apps/web/src/services/schema.d.ts；均无对方进行中任务占用。留给 T-0021：sync 接口、Webhook、Worker 对账与定时清理过期幂等记录（repositories.integrations.purge_expired_idempotency 已就绪），worker 需要 CREDENTIAL_ENCRYPTION_KEY 与 INTEGRATION_ALLOWED_HOSTS。
- 2026-10-30 15:14 UTC · claude · status in_progress → done：验收标准全部满足：迁移、Connector 分层与出站策略、项目 / 任务读写、Idempotency-Key 与未知状态、契约测试（mock + 真实实例）、审计、make lint / test / openapi 通过
- 2026-10-30 15:17 UTC · claude · 提交前自审补充：创建 / 修改 / 删除时 Vikunja 返回 401（Token 被拒）不再保存失败结果，Token 恢复后可用同一个键重试（契约第 7 节表格同步更新）；新增对应测试，后端共 193 个（189 通过 + 4 个真实实例测试在未设置 TEST_VIKUNJA_* 时跳过）。
