# 上下文用量、压缩与钩子的核验记录（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md）

> 规则（`docs/AGENTS.md` 第 8 节）：第三方的接口与行为必须在锁定版本上核验，不按名称猜；核验不了的写明，并留好退路。
> 2026-11-16 在本会话的环境里做：下载锁定版本的程序包，看类型定义、生成的接口定义、程序包里的文档和程序文件。
> 没有任何平台的账号，**没有跑真实回合**；下面写的是“程序里有这样的接口 / 字段”，不是“实测这样表现”。

## 1. Claude Code 2.1.323

来源：`@anthropic-ai/claude-code-linux-x64@2.1.323`（`claude --version` 输出 `2.1.323 (Claude Code)`）；
与它配套的 `@anthropic-ai/claude-agent-sdk@0.3.323`（`package.json` 里 `claudeCodeVersion = 2.1.323`）的 `sdk.d.ts`。

| 项 | 结果 | 出处 |
|---|---|---|
| 压缩事件 | `{"type":"system","subtype":"compact_boundary","compact_metadata":{"trigger":"manual"\|"auto","pre_tokens":N,"post_tokens"?:N,"duration_ms"?:N}}` | `sdk.d.ts` 的 `SDKCompactBoundaryMessage` |
| 窗口大小 | 回合结果 `modelUsage[<模型>].contextWindow` | `sdk.d.ts` 的 `ModelUsage` |
| 上下文用了多少 | 没有直接的字段。`SessionStartHookInput` 的说明里“上一次回答的窗口”= input + cache_read + cache_creation + output，用每次模型调用的 `message.usage` 照此算 | `sdk.d.ts` 第 7080 行附近 |
| `--settings <file-or-json>` | 有：从文件或 JSON 字符串加载额外设置（钩子写在设置里） | `claude --help` |
| PreCompact 钩子 | 输入 `trigger`、`custom_instructions`；钩子的输出作为 `newCustomInstructions` 并进压缩说明 | `sdk.d.ts` 的 `PreCompactHookInput`；程序文件里 `aHt(..., Io.newCustomInstructions)` |
| PostCompact 钩子 | 输入 `trigger`、`compact_summary`（压缩写出的摘要） | `sdk.d.ts` 的 `PostCompactHookInput` |
| SessionStart 钩子 | `source` 有 `compact`；输出的 `hookSpecificOutput.additionalContext` 放进上下文 | `sdk.d.ts` 的 `SessionStartHookInput`、`SessionStartHookSpecificOutput` |
| 手动压缩 | 非交互模式把 `/compact [说明]` 当提问发 | 方案第 2 节（T-0255 核对）；本次没有再核 |

## 2. CodeBuddy Code 2.184.0

来源：`@tencent-ai/codebuddy-code@2.184.0` 的 `dist/web-ui/docs/` 里的中文文档、`dist/codebuddy-headless.js`。

| 项 | 结果 | 出处 |
|---|---|---|
| 压缩事件 | 只有状态事件：`{"type":"system","subtype":"status","status":"compacting"}`，结束时 `status: null`；没有压缩前后的 token 数 | 程序文件里的 StreamJsonView（`status:eu?"compacting":null`） |
| 上下文用量 | 回合结果 `_meta["codebuddy.ai/contextUsed"]`、`_meta["codebuddy.ai/contextWindow"]`（“当前上下文占用快照，不要与计费 token 相加”） | 文档 `cli/stream-json-turn-usage.md` 第 9 节；程序文件里的常量 |
| 调触发点 | 环境变量 `CODEBUDDY_AUTOCOMPACT_PCT_OVERRIDE`（1–100）；`CODEBUDDY_AUTO_COMPACT_WINDOW`；参数 `--autocompact` | 文档 `cli/env-vars.md`、`cli/cli-reference.md` |
| `--settings` | 有：从 JSON 文件或字符串加载额外设置 | 文档 `cli/cli-reference.md` |
| 钩子 | 有 `PreCompact`（matcher manual / auto）、`PostCompact`（输入带 `compact_summary`，只用于观察）、`SessionStart`（matcher 有 `compact`，输出放进上下文或用 `additionalContext`） | 文档 `cli/hooks.md` |
| PreCompact 的输出怎么用 | 文档只写了退出码 2 会阻止压缩，**没有写输出会不会并进压缩说明**：未核实 | 同上 |
| 手动压缩 | 斜杠命令表里 `/compact` 标“支持”；**非交互模式（`-p`）能不能用，文档没说**：未核实 | 文档 `cli/slash-commands.md` |

## 3. Codex 0.169.0

来源：`@openai/codex@0.169.0-linux-x64`（`codex --version` 输出 `codex-cli 0.169.0`）；`codex app-server generate-json-schema` 生成的定义。

| 项 | 结果 | 出处 |
|---|---|---|
| 上下文用量 | 通知 `thread/tokenUsage/updated`：`tokenUsage.last`（最近一次调用，含 `totalTokens`、`reasoningOutputTokens`）、`tokenUsage.total`、`tokenUsage.modelContextWindow` | `v2/ThreadTokenUsageUpdatedNotification.json` |
| 压缩事件 | 会话条目 `{"type":"contextCompaction","id":...}`（`item/started`、`item/completed`）；旧的通知 `thread/compacted` 已标为弃用。都没有前后的 token 数和摘要 | `ThreadItem` 的 `ContextCompactionThreadItem`；`ContextCompactedNotification` |
| 手动压缩 | 请求 `thread/compact/start {threadId}`，应答 `{}`（不能带说明） | `ClientRequest.json` 的 `ThreadCompactStartParams` |
| 调触发点 | 配置项 `model_auto_compact_token_limit`（token 数）；`thread/start`、`thread/resume` 的参数里有 `config`（任意配置项） | 程序文件里的 `ConfigToml` 字段；`ThreadStartParams.config` |
| 钩子 | 事件名里有 `preCompact`、`postCompact`、`sessionStart`；配置文件的确切位置和压缩后钩子的输入字段**未核实** | `HookEventName`；程序文件里有 `hooks.json` 字样 |

## 4. 因此的实现约定

- 上下文用量、压缩次数（第 1 期）：三家都按上面核对过的字段读；字段没给就是“没报”（null），不估算。
  CodeBuddy 的压缩从“开始压缩”到“状态变回空”算一次；Codex 的 `contextCompaction` 条目算一次。
- 手动压缩（第 2 期）：Claude Code 发 `/compact [说明]`；Codex 调 `thread/compact/start`（不带说明）；
  CodeBuddy 也发 `/compact`，但结果以有没有收到压缩事件为准——没收到就照实告诉调用方“没有压缩”，并建议改用“带着摘要另开会话”。
- 调触发点（第 2 期）：Claude、CodeBuddy 用上面的环境变量（百分比）；Codex 用 `config.model_auto_compact_token_limit`（token 数）。
- 钩子（第 4 期）：只给 Claude Code 和 CodeBuddy Code 装，经 `--settings` 按回合带上；Codex 的钩子位置没核实，不装。
  CodeBuddy 的 PreCompact 输出是否生效没核实：仍然装上（无害），以有没有效果为准。整个钩子功能带开关，默认关，
  在真实账号上验证后再打开（方案第 8 节）。
