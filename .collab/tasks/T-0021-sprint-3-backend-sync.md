---
id: T-0021
title: Sprint 3 后端（二）：Webhook 签名校验与 Inbox、Worker 重试与死信、周期对账、tombstone 与同步 cursor（S3-05、S3-06 后端）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0020
created: 2026-10-30
updated: 2026-10-30
---

# T-0021 Sprint 3 后端（二）：Webhook 签名校验与 Inbox、Worker 重试与死信、周期对账、tombstone 与同步 cursor（S3-05、S3-06 后端）

## 目标
实现 Sprint 3 后端的第二部分：外部改动回到工作台的两条路径——Webhook（快但可能丢）与周期对账（兜底），
保证“外部原生任务改动最终出现在工作台”和“模拟 webhook 丢失可经对账补齐”（Sprint 3 DoD）。

## 验收标准
- [x] S3-05：迁移 `webhook_inbox`、`sync_cursors`；`POST /api/v1/webhooks/vikunja/{integration_id}` 按 T-0018 核验的方式校验签名（供应商支持时含时间戳），限制 body 大小，校验后落库并快速返回 2xx，payload 不信任
- [x] S3-05：Worker 用 `FOR UPDATE SKIP LOCKED`（或等效）领取事件，状态 pending → processing → completed，失败 retry（指数退避），超过阈值进 dead_letter；同一事件重复送达最多执行一次有效副作用（A08）
- [x] S3-06：ARQ 定时对账（增量 + 定期全量），用 `sync_cursors` 记录进度；外部删除写 tombstone（`deleted_at`）；乱序更新以外部的更新时间为准，旧事件不覆盖新数据；进程重启后可继续
- [x] S3-06：A09 模拟 webhook 丢失，对账最终纠正投影；首页 `tasks` 模块按投影新鲜度显示 ok / stale
- [x] 测试覆盖重复送达、乱序、签名错误、超大 body、重试与死信、重启恢复；`make lint`、`make test`、`make openapi` 通过

## 背景 / 约束 / 参考
- 依赖：T-0020 合并后开始（共用迁移链与 Connector）
- 规范：第 3 节（Webhook 先校验再落库）、第 5.1 节第 15、16 项与 5.2 节 DDL、第 9 节（Webhook 安全）、第 10 节（异步同步、幂等与恢复）；验收场景 A08、A09

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 15:22 UTC · claude · 开始任务
- 2026-10-30 15:50 UTC · claude · 完成后端（二）：迁移 0006（webhook_inbox，部分索引含 processing 作租约）；Webhook 端点（HMAC-SHA256 常量时间比较、集成不存在 / 停用 / 签名错一律 401 且不存在时也算一次 HMAC、256 KiB 上限 413、非 JSON 400、event_key = sha256(原始请求体) 去重、payload 只存事件名 / 时间 / ID）；Worker 作业：process_webhooks（SKIP LOCKED 领取、5 分钟租约、按 ID 读 Vikunja 当前状态写投影、404/403 tombstone、项目删除连带任务、30s×2^n ±20% 退避、8 次后 dead_letter、Token 被拒直接 dead_letter 并标 unauthorized）、reconcile（每 5 分钟增量，超过 1 小时做全量）、sync_integration（手动全量）、register_webhooks、purge；Redis 同步锁（SYNC_IN_PROGRESS）；Webhook 自动注册（未归档项目、按 target_url 幂等、401 停止、403 跳过）。真实容器实测：测试连接后约 1 秒注册好 Webhook；Vikunja 里改 / 建 / 删任务约 1 秒回到投影；停掉 caddy 让 Webhook 丢失后手动同步 2 秒内纠正（A09）。测试：后端 234 个（230 通过 + 4 个真实实例测试未设置环境变量时跳过）。越界说明：compose.yaml（worker 增加 CREDENTIAL_ENCRYPTION_KEY、INTEGRATION_ALLOWED_HOSTS、WEBHOOK_BASE_URL）、.env.example（WEBHOOK_BASE_URL）、docs（契约 9 / 10.1 节、数据字典、部署、接口、backend-stack ADR）；均无对方进行中任务占用。留给 T-0023：CI 里用真实 Vikunja 跑 A06 ~ A09 与浏览器 E2E。
- 2026-10-30 15:50 UTC · claude · status in_progress → done：验收标准全部满足：Webhook 签名校验与 Inbox、Worker 重试 / 死信 / 重启恢复、周期对账（增量 + 全量、tombstone、游标）、A08 / A09 测试与真实容器实测、make lint / test / openapi 通过
