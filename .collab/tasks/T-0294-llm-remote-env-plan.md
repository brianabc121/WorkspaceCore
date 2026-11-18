---
id: T-0294
title: 大模型客户端：开发环境的“云端远程容器”方案与核验
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/v2,docs/decisions,docs/AGENTS.md
depends_on:
created: 2026-11-18
updated: 2026-11-18
---

# T-0294 大模型客户端：开发环境的“云端远程容器”方案与核验

## 目标
人类 2026-11-18 在对话中提出（原话）：“我需要把现在workspace平台。大模型客户端这个页面内的，开发环境这个子页面内，新增开发环境这一栏增加一个云端远程容器的选项，具体功能如下：能够为容器服务器页面新增一个容器，作为codex/claude/codebuddy的云端环境使用 根据以上需求，给我写一份设计方案”。
看过方案草案后答复（原话）：“按照推荐的来”。即方案第 17 节 D1 ~ D8 全部按推荐，其中 D6 是在 `docs/AGENTS.md` 例外九末尾补一句。

本任务只做调研、方案和核验，实现见 T-0295。

## 验收标准
- [x] `docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md`：需求、整体做法、镜像、凭据、网络、资源与回收、页面、权限与安全、实施拆分、核验清单（第 16 节）、决定（第 17 节，记下人类的答复）、接口契约（第 21 节）、实施记录与偏差（第 22 节）
- [x] `docs/v2/README.md` 加一行
- [x] 核验记录 `docs/decisions/2026-11-18-llm-remote-env-verification.md`：在本会话自己的 Docker 29.9.2 + gVisor release-20261019.0 上核验 V2、V3（不登录的部分）、V6、V8、V9，写明版本与结果；V1、V4、V5、V7 要真实账号，写明“未测”、留给人类验收
- [x] `docs/AGENTS.md` 第 8 节例外九末尾补一句（D6）：开发环境可以是管理员在容器服务器上建的远程开发容器，建、改、删只能由 owner 在页面上做，智能体不能经这些工具建、改、删容器，沿用例外八的宿主保护；写法与其他例外一致，保护项不放宽
- [x] `python3 scripts/collab.py check` 通过

## 背景 / 约束 / 参考
- 编号由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留（[预留记录](https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6979130096)），和 T-0295 在同一个 PR #257 里。
- 参考：`docs/v2/LLM_BRIDGE_PLAN.md`、`LLM_BRIDGE_LONGRUN_PLAN.md`、`RUNNER_PLAN.md`、`docs/AGENTS.md` 例外八、例外九、例外十。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 13:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-18 13:57 UTC · claude · 开始任务
- 2026-11-18 13:58 UTC · claude · 方案 docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 写完；人类答复“按照推荐的来”，D1 ~ D8 按推荐（第 17 节）。核验记录 docs/decisions/2026-11-18-llm-remote-env-verification.md：本会话自己的 Docker 29.9.2 + gVisor release-20261019.0 上测了 V2、V3（不登录的部分）、V6、V8、V9；V1、V4、V5、V7 要真实账号，留给人类验收。docs/AGENTS.md 例外九末尾补了 D6 那一句。实施中与方案不同的地方记在第 22 节（含默认网络是“全部外网”、旧接入包的版本检查）。collab check 通过。
- 2026-11-18 13:58 UTC · claude · status in_progress → done：验收标准全部勾选
