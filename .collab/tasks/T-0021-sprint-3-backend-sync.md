---
id: T-0021
title: Sprint 3 后端（二）：Webhook 签名校验与 Inbox、Worker 重试与死信、周期对账、tombstone 与同步 cursor（S3-05、S3-06 后端）
owner: claude
status: todo
priority: P1
branch:
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
- [ ] S3-05：迁移 `webhook_inbox`、`sync_cursors`；`POST /api/v1/webhooks/vikunja/{integration_id}` 按 T-0018 核验的方式校验签名（供应商支持时含时间戳），限制 body 大小，校验后落库并快速返回 2xx，payload 不信任
- [ ] S3-05：Worker 用 `FOR UPDATE SKIP LOCKED`（或等效）领取事件，状态 pending → processing → completed，失败 retry（指数退避），超过阈值进 dead_letter；同一事件重复送达最多执行一次有效副作用（A08）
- [ ] S3-06：ARQ 定时对账（增量 + 定期全量），用 `sync_cursors` 记录进度；外部删除写 tombstone（`deleted_at`）；乱序更新以外部的更新时间为准，旧事件不覆盖新数据；进程重启后可继续
- [ ] S3-06：A09 模拟 webhook 丢失，对账最终纠正投影；首页 `tasks` 模块按投影新鲜度显示 ok / stale
- [ ] 测试覆盖重复送达、乱序、签名错误、超大 body、重试与死信、重启恢复；`make lint`、`make test`、`make openapi` 通过

## 背景 / 约束 / 参考
- 依赖：T-0020 合并后开始（共用迁移链与 Connector）
- 规范：第 3 节（Webhook 先校验再落库）、第 5.1 节第 15、16 项与 5.2 节 DDL、第 9 节（Webhook 安全）、第 10 节（异步同步、幂等与恢复）；验收场景 A08、A09

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
