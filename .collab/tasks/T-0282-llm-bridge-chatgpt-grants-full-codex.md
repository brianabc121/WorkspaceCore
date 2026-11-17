---
id: T-0282
title: 原来只给了 ChatGPT 的实例授权改成整个 Codex（对话、开发都能用；迁移 0078）
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/api/app/modules/llm_bridge,services/api/migrations/versions,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs/api-contract.md,docs/v2/LLM_BRIDGE_PLAN.md
depends_on: T-0280
created: 2026-11-17
updated: 2026-11-17
---

# T-0282 原来只给了 ChatGPT 的实例授权改成整个 Codex（对话、开发都能用；迁移 0078）

## 目标
人类 2026-11-17 在对话中要求（编号在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6944640293）：
T-0280 把只给了 ChatGPT 的实例迁成了“Codex（只能对话）”（`codex:chat`），这些实例也要能用 Codex 的开发模式。

## 验收标准
- [x] 迁移 0078：实例授权里的 `codex:chat` 换成 `codex`，去重并保留顺序；其他平台和空列表（都允许）不动；可以重复执行，downgrade 不做任何事。已经跑过 0077 的库也能改过来，所以不改 0077 本身。
- [x] 接口：授权里收到旧名字 `chatgpt` 时按整个 `codex` 处理（不再当成 `codex:chat`）。
- [x] “Codex（只能对话）”选项保留，由管理员手动选；页面去掉“原来只勾了 ChatGPT 的实例升级后是这一项”。
- [x] 测试：迁移 0077 → 0078 的结果、0078 单独的各种组合；接口别名；原来只给了 ChatGPT 的实例给了开发环境就能建开发会话；只能对话的授权照旧碰不到开发会话；页面说明。
- [x] `docs/api-contract.md`、`docs/v2/LLM_BRIDGE_PLAN.md` 跟着改。

## 说明
- 权限变化：这些实例能用开发模式，但新建开发会话还要管理员给它勾开发环境（`environment_ids` 为空就一个都不能用）；读、续发别人的开发会话还要有“接手别人的会话”权限。
- 从 0077 升级到 0078 之间，管理员在页面上特意选的“Codex（只能对话）”也会一起换成 `codex`（这个选项是 T-0280 才有的，时间很短），要限制的话在页面上重新选。
- 管理器（`services/llm-bridge`）不改：请求头里的旧名字 `chatgpt` 只会来自没升级的工作台，那里的 `chatgpt` 原本就只能聊天，按 `codex:chat` 算仍然对。镜像标签不变。

## 进度日志
- 2026-11-17 claude：人类在对话中提出，编号在 #235 预留后开工。
- 2026-11-17 10:09 UTC · claude · status in_progress → done：迁移 0078（codex:chat → codex）、接口别名 chatgpt → 整个 codex、授权页说明与测试、文档已完成；本地检查见 PR
