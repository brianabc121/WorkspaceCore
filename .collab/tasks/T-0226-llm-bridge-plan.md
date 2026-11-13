---
id: T-0226
title: 大模型会话桥方案：一个 Docker 部署的 MCP 服务，让多个 OpenClaw 向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话（调研 + 设计）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: docs/v2
depends_on:
created: 2026-11-13
updated: 2026-11-13
---

# T-0226 大模型会话桥方案：一个 Docker 部署的 MCP 服务，让多个 OpenClaw 向 ChatGPT / Codex / Claude / WorkBuddy 发指令并管理会话（调研 + 设计）

## 目标
部署在 Docker 上的一个 MCP 服务（“大模型会话桥”），让多个 OpenClaw 经它向 ChatGPT / Codex / Claude / WorkBuddy 发指令：
多个智能体同时调用、跨平台管理多个会话、查看账号下的会话列表、新建会话时选模型（开发模式下选环境）；
一个 MCP 服务每个平台只管一个账号，多账号靠多部署几个容器。本任务只做调研和设计方案，不写实现代码。

## 验收标准
- [x] 调研 GitHub 上的同类开源方案并附来源（做法、会话管理、多调用方、登录方式、活跃度、许可证）
- [x] 核验各平台的官方可编程接口与条款（Codex、ChatGPT、Claude、WorkBuddy / CodeBuddy、OpenClaw 接 MCP 的方式），核验不了的标明
- [x] `docs/v2/LLM_BRIDGE_PLAN.md`：业内做法、总体架构、会话模型、MCP 工具、各平台适配、单账号约束、部署、安全、待人类决定的事项、任务拆分、风险
- [x] `docs/v2/README.md` 加一行

## 背景 / 约束 / 参考
- 人类 2026-11-13 在对话中提出并指派（需求原文写进方案开头）。
- `docs/AGENTS.md` 第 8 节：不得发明第三方 API；OpenClaw 的工具限制不放宽；外部 HTTP 要有 timeout、错误归类、重试限制。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 09:38 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 09:38 UTC · claude · 开始任务
- 2026-11-13 09:39 UTC · claude · 人类 2026-11-13 在对话中指派：新开分支，先调研 GitHub 同类开源方案和业内做法，再写大模型会话桥（MCP）的设计方案。任务建好时 owner 已是 claude，所以没有另记 set --as human。两路调研进行中；下一步：按调研结果写 docs/v2/LLM_BRIDGE_PLAN.md。
- 2026-11-13 09:45 UTC · claude · status in_progress → done：方案（待批准）docs/v2/LLM_BRIDGE_PLAN.md 已写好：开源方案调研附来源，各平台官方接口与条款的核验结果（未核验的已标明），架构、会话与回合、驱动、环境、单账号约束、工具、部署、安全；D1 ~ D9 待人类决定，例外九只是建议写法、未写进 AGENTS.md；实施任务批准后再建。
