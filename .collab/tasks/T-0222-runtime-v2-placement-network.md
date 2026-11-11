---
id: T-0222
title: 按人类确认设计落实双服务器池调度、容器配额审批与跨节点网络
owner: codex
status: in_progress
priority: P1
branch: codex/T-0222-runtime-v2-placement-network
paths: services/api,apps/web,infra,docs/superpowers,docs/decisions,docs/verification,.collab/tasks,.collab/handoffs,.env.example,compose.yaml,.github/workflows
depends_on:
created: 2026-11-11
updated: 2026-11-11
---

# T-0222 按人类确认设计落实双服务器池调度、容器配额审批与跨节点网络

## 目标
人类于2026-11-11明确授权按已确认设计全面实施。以origin/main a1053c7为基线，落实双池默认自动/可指定调度、5容器配额与独立审批、同实例跨服务器网络、严格服务入站及配套UI。完整方案和实施计划见docs/superpowers/specs/2026-11-11-runtime-v2-design.md及docs/superpowers/plans/2026-11-11-runtime-v2.md。

## 验收标准
- [ ] 双服务器池auto/specified端到端接通，容量预约并发安全，实际宿主可追踪。
- [ ] 默认5保有环境与独立critical权限审批可申请、撤销和审计。
- [ ] 新运行后端跨节点同实例私网，跨实例与公网入站明确鉴权。
- [ ] 前端页面与正式OpenAPI一致，必要测试/构建/迁移通过，实际日志已记录。
- [ ] 独立审查、交付PR和清晰部署/回退文档；未验证的外部环境能力如实列明。

## 背景 / 约束 / 参考
当前主干无进行中冲突任务或打开PR。旧/workspace/WorkspaceCore含T-0149遗留未提交修改，不碰该目录；本任务使用独立检出/workspace/WorkspaceCore-runtime-v2。人类授权跨原模块归属实施本设计，协作协议不改。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 05:10 UTC · codex · 创建任务（owner: codex）
- 2026-11-11 05:10 UTC · codex · 开始任务
- 2026-11-11 08:31 UTC · codex · 人类明确要求全面实施；已从a1053c7新建专属分支，旧T0149未提交工作未动。计划/设计已落盘，双池调度/权限/UI正在集成；纯Placement22测试通过。已部署本地PostgreSQL16/Redis7测试服务，跨节点网络尚未实测，K8s仍未启用。审阅发现重启预约被旧上报释放与旧采样重复使用两处竞态，正在加回归修复。
- 2026-11-11 14:58 UTC · codex · 继续集成：类型检查287文件、Ruff、核心60单元检查及宿主网络认证/未验证拒收回归通过。新建docs/verification/2026-11-11-runtime-v2/PROGRESS.md列明真实结果与未完成工作；Kubernetes调度/通信数据面仍关闭，完整前端suite有失败正在处理，不标done。
