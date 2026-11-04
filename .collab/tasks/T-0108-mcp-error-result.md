---
id: T-0108
title: MCP 工具错误不带结构化内容：OpenClaw 的 MCP 客户端会把它当格式错误丢掉，智能体看不到错误码
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,tests/e2e,docs
depends_on:
created: 2026-11-04
updated: 2026-11-04
---

# T-0108 MCP 工具错误不带结构化内容：OpenClaw 的 MCP 客户端会把它当格式错误丢掉，智能体看不到错误码

## 目标
T-0106 的 agents CI 日志里，甲第 6 次删除被拦下时 OpenClaw 记的是 `MCP error -32602: Structured content does not match the tool's output schema`。
原因：锁定的 OpenClaw 2026.9.36 带的 `@modelcontextprotocol/sdk` 1.32.1，客户端 `callTool` 只要结果里有 `structuredContent` 就按工具的 outputSchema 校验，
错误结果也不例外；工作台的错误结果带着 `{"error": {...}}` 的结构化内容，于是每个工具错误（没权限、找不到、参数错、限流、删除已暂停……）
到了智能体那里都变成同一个格式错误，看不到错误码和说明。操作本身照样被拦下，不影响权限边界。

## 验收标准
- [x] MCP 工具错误只用文字内容返回 `{"error": {"code", "message"}}` 的 JSON，`isError: true`，不带 `structuredContent`（`app/domain/mcp_tools.py`）；运维指标的结果码照旧
- [x] 后端测试改为从文字内容读错误码，并断言没有结构化内容（`tests/helpers.py` 的 `tool_error`）
- [x] agents CI 场景 15：被拦下的删除，甲拿到的工具结果里有 `DELETES_PAUSED`，没有“does not match the tool's output schema”
- [x] OC-1 契约第 9.4 节、OC-5 ADR 的错误格式改写并注明原因

## 背景 / 约束 / 参考
- 核验：`docker run --entrypoint sh openclaw/openclaw:2026.9.36 -c "grep -n -B12 'does not match the tool' /app/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js"`
  （第 486 ~ 498 行：有 outputSchema 的工具，没有结构化内容且不是错误时报 InvalidRequest；有结构化内容就校验，不符合报 InvalidParams）
- MCP 规范允许错误结果不带结构化内容（SDK 的注释：“unless it's an error”）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 05:52 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 06:21 UTC · claude · 开始任务
- 2026-11-04 06:24 UTC · claude · 实现：mcp_tools.error_result 不再带 structured_content（错误码与说明在文字内容的 JSON 里），运维指标从文字内容取错误码；tests/helpers.py 加 tool_error（断言 isError、没有结构化内容），test_mcp_api / test_mcp_read_tools / test_oc6_security / test_knowledge_tools 改用它；agents 场景 15 检查被拦下的删除结果里有 DELETES_PAUSED；OC-1 契约 9.4、OC-5 ADR 改写错误格式并注明原因。检查：ruff、ruff format、mypy（270 个文件）、openapi 最新；MCP 与工具相关的 7 个测试文件 57 passed。场景 15 的新检查以本 PR 的 agents CI 为准。
- 2026-11-04 06:24 UTC · claude · status in_progress → done：错误结果只用文字内容；测试、场景 15 与契约已更新
