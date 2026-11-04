---
id: T-0115
title: 智能体运维升级验收与文档：agents CI（真实指标、SSH 动作）、浏览器验收、威胁模型、部署与运维手册
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github,docs,apps/web,services/api,scripts
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0115 智能体运维升级验收与文档：agents CI（真实指标、SSH 动作）、浏览器验收、威胁模型、部署与运维手册

## 目标
方案第 10 节的验收与文档：在真实 OpenClaw 与 SSH 部署上验证指标上报和运维动作，在浏览器里验证新建项目勾选智能体、自由接取与打回、服务器状态与运维动作；更新威胁模型、部署与运维手册。

## 验收标准
- [ ] agents CI：真实 OpenClaw 容器上报指标；SSH 部署验收逐个执行运维动作并核对 `docker compose` 输出格式
- [ ] agents 场景 17（甲、乙同时领取只成功一个，完成后打回再完成）、场景 18（没开权限被拒，开了之后建项目并拉另一个实例）
- [ ] 浏览器验收：新建项目勾选智能体、项目群；自由接取与打回；服务器状态面板；运维动作
- [ ] 威胁模型、架构、部署手册、release checklist、versions 更新；方案状态改为已实施

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
