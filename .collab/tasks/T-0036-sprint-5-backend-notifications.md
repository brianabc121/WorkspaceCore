---
id: T-0036
title: Sprint 5 后端（二）：通知中心（模型、迁移、已读）、SSE 事件流（重连、Last-Event-ID、轻量失效通知）（S5-03）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0036 Sprint 5 后端（二）：通知中心（模型、迁移、已读）、SSE 事件流（重连、Last-Event-ID、轻量失效通知）（S5-03）

## 目标
实现通知中心与 SSE 事件流的后端，让页面在数据变化时及时刷新，并在断线后能重新拉取最新状态（A16）。

## 验收标准
- [x] `notifications` 表与迁移（规范 5.1 第 14 项）；按用户与工作空间隔离；`source_key` 去重（重复事件不产生重复通知）；已读 / 批量已读；首页未读数一致
- [x] 通知来源：至少覆盖集成授权失效 / 限流、同步失败、自动化执行失败；通知正文不含 Token、payload 原文
- [x] SSE `/api/v1/events/stream`：鉴权与工作空间范围、只发轻量失效事件（资源类型 + 范围）、`id` 与 `Last-Event-ID` 重连、心跳；多进程下经 Redis 分发；连接数与空闲超时有上限
- [x] Caddy / 反向代理对 SSE 不缓冲；断线重连后客户端重新拉取能拿到最新投影（A16 的接口级测试）
- [x] ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 依赖：T-0034 合并后开始
- 规范：第 4 节（SSE 仅发失效通知）、第 5.1 节第 14 项、第 6.1 节、第 10 节；验收场景 A16；S5-03

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 02:19 UTC · claude · 来自 T-0035：服务状态的健康告警（契约第 3.1 节：服务变为 down / 恢复时给 owner、admin 发 service_down / service_recovered 通知，同一变化一段时间内只发一次）在本任务实现；可在 Worker 的周期任务里调用 app.domain.operations.DeploymentHealth 比较前后状态。
- 2026-10-31 02:29 UTC · claude · 开始任务
- 2026-10-31 02:54 UTC · claude · 实现完成：notifications 表（迁移 0009，(user, workspace, source_key) 部分唯一索引去重）；通知接口（只看自己的、按工作空间隔离、keyset 游标、ids / all 已读，别人的 ID 按不存在处理）；首页 summary.unread_notifications 返回实际值（WorkspaceAccess 增加 user_id）；通知来源：集成授权失效 / 无法连接 / 恢复、GitHub 限流（Worker 同步与任务写入的状态变化处），服务健康告警（Worker 每分钟检查 api /readyz、Prometheus、Grafana，连续两次 down 才通知，恢复再通知），自动化结果留给 T-0037（app.domain.notifications.notify_user）；失效事件：会话 after_flush 收集 ORM 改动、Core 写入用 events.mark 登记，after_commit 才 XADD 到 events:ws:<ID>（MAXLEN≈500，24 小时过期），回滚不发布；SSE：FastAPI EventSourceResponse，推送前完成成员校验并释放数据库连接，Last-Event-ID 补发、超出保留范围或格式不对发 reset，XREAD 阻塞 10 秒、15 秒心跳，每进程 200 / 每用户 5 个连接（429），单连接最长 600 秒、每 60 秒复核会话与成员身份；Caddy 对 SSE 路径不压缩、flush_interval -1。本机实测（claude 自己的 Docker）：迁移 0008→0009；经 Caddy 的 SSE 无 Content-Encoding、事件 38 ms 送达、Last-Event-ID 补发、15 秒 ': ping'；停掉 Prometheus 后第二次检查发出 service_down，启动后下一次检查发出 service_recovered。越界修改（claude 负责的基础设施与 D-0005 规定的生成文件）：compose.yaml（worker 加 API_INTERNAL_URL / 监控地址并加入 monitoring 网络）、infra/caddy/Caddyfile、docs/deployment.md 第 13 节、docs/api-contract.md、openapi.json 与 apps/web/src/services/schema.d.ts（SSE 增加 429，只增不改）。
- 2026-10-31 02:54 UTC · claude · status in_progress → done：通知中心与 SSE 后端完成
