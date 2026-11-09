---
id: T-0180
title: 智能体服务器（三）：一次点击部署（幂等收据）、智能体总览与服务器部署归属
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/agent_deployments.py,services/api/app/domain/agent_overview.py,services/api/app/domain/agents.py,services/api/app/domain/agent_servers.py,services/api/app/domain/agent_ssh_install.py,services/api/app/api/v1/agents.py,services/api/app/api/deps.py,services/api/app/schemas/agents.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts
depends_on: T-0179
created: 2026-11-09
updated: 2026-11-09
---

# T-0180 智能体服务器（三）：一次点击部署（幂等收据）、智能体总览与服务器部署归属

## 目标
“部署智能体”一次点击：新建实例与部署任务在一个事务里接纳，带 request_id 收据，响应丢失可查询、失败继续原实例；提供智能体总览与服务器的部署归属、统计。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 3）

## 验收标准
- [x] `POST /agents/deployments`（request_id 即幂等键）：同键并发只建一个实例与任务；同服务器不同键并发另一个 409 且不留多余实例；校验失败不留半个实例
- [x] `GET /agents/deployments/requests/{request_id}` 收据：只给原用户与工作空间，返回同一实例与任务
- [x] `GET /agents/overview`：summary 全量统计（不受分页与状态筛选影响），GET 不访问 SSH / OpenIM，无逐行 N+1
- [x] 服务器列表 summary 与 initialization_status / deployable / deploy_block_reason / managed_deployments / successful_deployments（旧未知为 null 并标 partial）/ 限额汇总；逐实例 deployment_state
- [x] 部署成功写 runtime_installed=true，卸载保留目录写 false，旧记录保持 null

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 09:19 UTC · claude · 开始任务
- 2026-11-09 10:17 UTC · claude · status in_progress → done：CI 42592666997 全绿（agents：SSH 验收全部通过、两页浏览器验收与 Runner 浏览器验收全部通过）；核验记录第 6 节已填
