---
id: T-0100
title: 知识库认知对齐与自我提升（项目简报已读、复盘定时任务、纠正回写、指标）与验收（K-5 + K-6）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,tests/e2e,.github/workflows,docs,scripts
depends_on: T-0099
created: 2026-11-03
updated: 2026-11-03
---

# T-0100 知识库认知对齐与自我提升（项目简报已读、复盘定时任务、纠正回写、指标）与验收（K-5 + K-6）

## 目标
完成 `docs/v2/KNOWLEDGE_PLAN.md` 的 K-5 与 K-6：
- 项目简报的已读记录与提醒（管理员以自己的 IM 账号发）；
- 每晚复盘定时任务：SSH 部署时用 `openclaw automations add` 建；
- 写入准则；智能体指标；
- 浏览器与真实 OpenClaw 验收，文档与威胁模型。

## 验收标准
- [ ] 简报已读、谁没读最新版、“现在发给它”
- [ ] 复盘定时任务随 SSH 部署创建（已有就跳过），CHECKLIST 给出手工命令
- [ ] 智能体知识库页的指标
- [ ] CI 的真实 OpenClaw 经 MCP 调用 knowledge_write / search / delete，直接生效、可撤销、超限暂停、密钥拦截
- [ ] 浏览器验收脚本覆盖知识库主要路径
- [ ] docs：threat-model、architecture、deploy-guide、versions；方案状态改为已完成

## 背景 / 约束 / 参考
- 方案：`docs/v2/KNOWLEDGE_PLAN.md`（已批准）；核验：`docs/decisions/2026-11-03-knowledge-k0-verification.md`
- 效果图：https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 20:45 UTC · claude · 创建任务（owner: claude）
