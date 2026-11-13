# 大模型会话桥（llm-bridge）

一个 Docker 部署的 MCP 服务：多个调用方（OpenClaw）经它向 ChatGPT / Codex / Claude / WorkBuddy 发指令、管理会话。
方案见 [`docs/v2/LLM_BRIDGE_PLAN.md`](../../docs/v2/LLM_BRIDGE_PLAN.md)，接口核验见
[`docs/decisions/2026-11-13-llm-bridge-verification.md`](../../docs/decisions/2026-11-13-llm-bridge-verification.md)。

```
bridge/
  app.py          HTTP 入口：POST /mcp、/admin/*、/healthz；令牌认证
  mcp_server.py   MCP 服务（SDK 2.3.0，无状态、JSON 响应）
  tools.py        10 个工具的名字、说明、参数
  engine.py       会话与回合：归属、排队、幂等、并发上限、账号一致性、重启恢复
  channels.py     平台 + 模式 → 账号槽位；哪些通道是接口桩
  store.py        SQLite（数据卷里的 bridge.sqlite3）
  redact.py       密钥样式打码
  drivers/
    base.py       驱动接口
    codex.py      codex app-server（JSON-RPC）
    cli_stream.py Claude Code 与 CodeBuddy Code 的无界面模式（两家格式相同）
    fake.py       测试用
tests/            test_bridge（端到端）、test_drivers（对仿真的官方程序）、test_live（对真实程序，默认跳过）
```

## 本地开发

```bash
cd services/llm-bridge
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy bridge && uv run pytest
# 对真实的官方命令行实测（不需要登录的部分）
BRIDGE_LIVE_CLI_DIR=<装有 codex / claude / codebuddy 的目录> uv run pytest tests/test_live.py
# 本地运行（fake 平台打开，方便不登录就试工具）
BRIDGE_DATA_DIR=./.data BRIDGE_ADMIN_TOKEN=$(openssl rand -hex 32) BRIDGE_ENABLE_FAKE=true \
  uv run uvicorn bridge.main:app --port 8787
```

## 认证

- **管理令牌**（`BRIDGE_ADMIN_TOKEN`）：调用 `/admin/*`；调用 `/mcp` 时用请求头说明代表哪个调用方：
  `X-Bridge-Caller`、`X-Bridge-Caller-Label`（URL 编码）、`X-Bridge-Scopes`（`write,account,takeover` 的子集）。工作台用这种方式。
- **调用方令牌**（`POST /admin/callers` 发放，只返回一次）：只能调用 `/mcp`，身份和权限以桥里登记的为准。OpenClaw 直连时用这种方式。

## 数据卷 `/data`

| 路径 | 内容 |
|---|---|
| `bridge.sqlite3` | 账号登记、设置、环境、会话、回合、事件 |
| `home/codex`、`home/claude`、`home/codebuddy` | 各平台官方程序的登录目录和会话记录（每个平台只有一个） |
| `secrets/` | 管理员在页面上填的 API 密钥 / 令牌（0600），只用来注入官方程序的环境变量 |
| `work/<会话号>/` | 每个会话的工作目录；归档会话时删除 |
