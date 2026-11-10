---
id: T-0205
title: 中转站管理（MC-5）：OpenClaw 配置生成、应用与真实联调
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/agent_model_config.py,services/api/app/agent_bundle,services/api/app/agent_provisioning,services/api/app/domain/agent_deployments.py,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0204
created: 2026-11-09
updated: 2026-11-10
---

# T-0205 中转站管理（MC-5）：OpenClaw 配置生成、应用与真实联调

## 目标
从绑定生成 OpenClaw 的受管模型配置（不含 Key），由实例上的受控配置助手原子写入、校验并应用，回报结果；接入包、取密钥程序与定时任务配套；真实 OpenClaw 联调。（MC-5）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] 配置片段只含连接生成的 provider 键与 SecretRef，保留渠道、MCP、工具限制等其他配置
- [ ] 新部署不再固定 openai / openai_api_key；旧实例保持旧配置（legacy_active）
- [ ] 配置助手先写临时文件、校验再替换，失败保留旧配置；未确认不显示已应用
- [ ] 真实 OpenClaw + 假模型服务：新连接、模型切换、Key 轮换、一台服务器两个实例互不影响

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 00:09 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · 本机真实 OpenClaw（model_config_openclaw.py）15/15；等 CI agents 场景 27（整套工作台 + 两个实例）通过后标 done
