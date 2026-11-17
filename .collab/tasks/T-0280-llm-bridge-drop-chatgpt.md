---
id: T-0280
title: MCP 管理器去掉单列的 ChatGPT 平台，只留 Codex（旧名字 chatgpt 按 codex 处理，库里旧记录迁移）
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge/bridge,services/llm-bridge/tests,services/llm-bridge/README.md,infra/llm-bridge/smoke.sh,services/api/app/modules/llm_bridge,services/api/migrations/versions,services/api/openapi.json,services/api/tests,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e,docs/api-contract.md,docs/deployment.md,docs/versions.md,docs/guides/mcp-manager-deploy.md,docs/v2/LLM_BRIDGE_PLAN.md
depends_on: T-0226
created: 2026-11-17
updated: 2026-11-17
---

# T-0280 MCP 管理器去掉单列的 ChatGPT 平台，只留 Codex（旧名字 chatgpt 按 codex 处理，库里旧记录迁移）

## 目标
人类 2026-11-17 在对话中要求：MCP 工具里去掉 “ChatGPT” 这个分类，不再区分 ChatGPT 和 Codex，只留 Codex。

依据：Codex 调研后确认，MCP 管理工具里的 “ChatGPT 聊天” 实际用的就是 Codex app-server 的纯聊天通道。
代码里也是这样：`bridge/drivers/codex.py` 开头写着 `chatgpt.chat` 也走 codex app-server，和 `codex.chat` 是同一条路。
单列一个平台，会让人以为有两个账号、两份额度。

## 验收标准
- [x] 管理器：
  - 通道表去掉 `chatgpt.chat`，`codex.chat` 的说明写明是纯聊天。
  - `chatgpt` 这个旧名字在以下地方都按 `codex` 处理：MCP 工具参数、`X-Bridge-Platforms` 请求头、设置里的 platforms、会话列表筛选、环境接口。
  - 旧库升级时：会话的平台改成 `codex`；额度和暂停记录并入 `codex`（`codex` 已有的为准）；设置里的 platforms 也改过来。升级可以重复执行。
- [x] 工作台：
  - `Platform` 去掉 `chatgpt`。接口和智能体工具收到旧名字照样接受，按 `codex` 处理；OpenAPI 和工具说明里只列现在的平台。
  - 读到没升级的管理器返回的 `chatgpt` 时：平台列表、用量、会话索引都换成 `codex`，通道表不再列 `chatgpt.chat`。
  - 授权里给了 `codex` 的，发给管理器的请求头里一并带上 `chatgpt`，这样没升级的管理器上的旧会话照样能用。
- [x] 迁移 0077：会话索引、群聊成员里的 `chatgpt` 改成 `codex`；实例授权、安装记录、部署任务的 platforms 列表里的 `chatgpt` 换成 `codex`，去重并保留顺序。downgrade 不做任何事。
- [x] 页面：
  - 平台选择、部署表单、添加管理器、实例授权、开发环境、聊天页、用量筛选都去掉 ChatGPT。
  - 账号卡片、已登录列表里的名字改成 Codex（登录照旧用 ChatGPT 账号）。
  - 新建会话时，Codex 有“对话”“开发”两种用法。
- [x] 测试：管理器、工作台（含迁移）、前端；端到端浏览器脚本和 smoke 脚本跟着改；文档更新。

## 背景 / 约束 / 参考
- 安全：实例授权里只勾了 “ChatGPT”（原来只能聊天）的，迁移后变成 “Codex”。
  - 开发模式另外要授权开发环境：`environment_ids` 为空就一个都不能用，没给环境的实例照样只能聊天。
  - Codex 的聊天通道是只读沙箱、空目录，原来的 ChatGPT 聊天走的也是它，能力没有变化。
- 并发：原来 ChatGPT 和 Codex 各算 3 个（同一个账号最多 6 个），现在 Codex 一个平台算 3 个（对话和开发合计）。需要更多的话，在“设置”里把每个平台的会话数调大。
- 管理器源码变了，镜像标签会变。没升级的管理器照样能用：工作台把它返回的 `chatgpt` 当成 `codex`，请求头里也替它带上 `chatgpt`。
- `docs/AGENTS.md` 第 8 节（人类制定的规则）里“ChatGPT / Codex / Claude / WorkBuddy”的说法没有改，留给人类决定。其他方案文档（MEMORY / QUOTA / QUEUE / CHAT 等）是当时的记录，只在 `LLM_BRIDGE_PLAN.md` 6.2 末尾补了更新说明。
- 交叠：T-0234（claude，进行中）不碰部署代码；T-0224（codex，handoff）的分支没有改这些文件。
- 编号由集成会话在 #235 预留（https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6941423577）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 07:10 UTC · claude · 创建任务（编号在 #235 预留，owner claude；人类在对话中直接派活）
- 2026-11-17 07:38 UTC · claude · 管理器、工作台（含迁移 0077）、页面、端到端脚本与文档完成；本地测试通过，等 PR #245 合并后推送并开 PR
- 2026-11-17 07:38 UTC · claude · status in_progress → done：实现与测试完成，随 PR 合并；已部署的管理器重新部署（升级）后才不再单列 chatgpt
