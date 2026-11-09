---
id: T-0200
title: 中转站管理（MC-0）：OpenClaw 与模型协议核验
owner: claude
status: in_progress
priority: P1
branch: b6-model-connections
paths: docs/decisions,docs/v2/model-connections
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0200 中转站管理（MC-0）：OpenClaw 与模型协议核验

## 目标
核验锁定的 OpenClaw 2026.9.36 的模型服务配置（models.providers 字段、api 取值、URL 拼接、鉴权头、热加载、配置校验）与各官方协议，写 ADR；只开放核验过的协议。（MC-0）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] ADR 记录 OpenClaw 配置字段、api 取值与 URL 拼接，附镜像内文件路径与原文摘录
- [ ] OpenAI 官方与 OpenAI 兼容中转的协议、URL、鉴权、模型列表与推理路径有官方来源
- [ ] Anthropic / Gemini 原生协议核验到哪一步如实记录，未核验的不开放（capabilities 不出现）
- [ ] 配置热加载与应用方式有实测证据

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 23:43 UTC · claude · 开始任务
