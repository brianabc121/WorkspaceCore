---
id: T-0207
title: 中转站管理（MC-7）：智能体连接分配、部署与应用状态
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/agents,apps/web/src/features/model-connections,apps/web/tests
depends_on: T-0204, T-0206
created: 2026-11-09
updated: 2026-11-10
---

# T-0207 中转站管理（MC-7）：智能体连接分配、部署与应用状态

## 目标
智能体连接分配：管理页分配表、部署抽屉的连接与主模型选择、智能体详情的模型连接面板与应用状态。（MC-7）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [x] 新建实例时列 owner 可用的启用且完整的连接，选择即授权；已有实例只列已授权的
- [x] 保存后显示待应用，实例确认后才显示已应用；失败 / 未知 / 需手动应用分别有操作
- [x] 有绑定的模型与授权不能直接移除，先改绑定
- [x] IM、MCP、唤醒、邮箱、资源上限等原有设置不受影响

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 00:24 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · status in_progress → done：部署抽屉必选主模型、实例页主模型面板、连接分配卡（预填当前目标）；vitest model-bindings 8 个通过
