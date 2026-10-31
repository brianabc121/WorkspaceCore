---
id: T-0036
title: Sprint 5 后端（二）：通知中心（模型、迁移、已读）、SSE 事件流（重连、Last-Event-ID、轻量失效通知）（S5-03）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0034
created: 2026-10-31
updated: 2026-10-31
---

# T-0036 Sprint 5 后端（二）：通知中心（模型、迁移、已读）、SSE 事件流（重连、Last-Event-ID、轻量失效通知）（S5-03）

## 目标
实现通知中心与 SSE 事件流的后端，让页面在数据变化时及时刷新，并在断线后能重新拉取最新状态（A16）。

## 验收标准
- [ ] `notifications` 表与迁移（规范 5.1 第 14 项）；按用户与工作空间隔离；`source_key` 去重（重复事件不产生重复通知）；已读 / 批量已读；首页未读数一致
- [ ] 通知来源：至少覆盖集成授权失效 / 限流、同步失败、自动化执行失败；通知正文不含 Token、payload 原文
- [ ] SSE `/api/v1/events/stream`：鉴权与工作空间范围、只发轻量失效事件（资源类型 + 范围）、`id` 与 `Last-Event-ID` 重连、心跳；多进程下经 Redis 分发；连接数与空闲超时有上限
- [ ] Caddy / 反向代理对 SSE 不缓冲；断线重连后客户端重新拉取能拿到最新投影（A16 的接口级测试）
- [ ] ruff / mypy / pytest 通过

## 背景 / 约束 / 参考
- 依赖：T-0034 合并后开始
- 规范：第 4 节（SSE 仅发失效通知）、第 5.1 节第 14 项、第 6.1 节、第 10 节；验收场景 A16；S5-03

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 00:56 UTC · claude · 创建任务（owner: claude）
