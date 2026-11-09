---
id: T-0182
title: 智能体服务器（五）：“OpenClaw 智能体”页面、选择服务器部署的抽屉、详情与恢复
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/AgentsPage.vue,apps/web/src/features/agents,apps/web/tests
depends_on: T-0181
created: 2026-11-09
updated: 2026-11-09
---

# T-0182 智能体服务器（五）：“OpenClaw 智能体”页面、选择服务器部署的抽屉、详情与恢复

## 目标
`/agents` 改为“OpenClaw 智能体”页：总览表、统计、筛选、详情，以及选择服务器自动部署的抽屉（带收据恢复）；保留手动登记与原有详情能力。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 5）

## 验收标准
- [ ] 列表与统计用 `/agents/overview`；连接状态、部署进度、心跳分别显示，没有证据不显示“已停止”
- [ ] 部署抽屉只能选可部署的服务器，不要密码 / IP；默认限额不限制；`?deploy=1&server_id=` 预选
- [ ] 连点用同一 request_id；响应丢失先查收据；失败继续原实例；sessionStorage 只存 request_id / 实例 ID / 用户与工作空间
- [ ] 手动登记 / 接入包、权限、运维、唤醒、发件箱等原有能力仍可用
- [ ] 新测试与既有 agents 测试通过；亮 / 暗、桌面 / 手机截图

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
