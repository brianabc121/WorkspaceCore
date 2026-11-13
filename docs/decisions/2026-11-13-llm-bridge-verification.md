# 大模型会话桥：官方命令行的版本锁定与接口核验（T-0227）

- 日期：2026-11-13
- 状态：accepted（claude；只影响会话桥自己的范围）
- 相关：`docs/v2/LLM_BRIDGE_PLAN.md`、`services/llm-bridge/`、`docs/versions.md` 第 1.6 节

项目规则要求第三方接口必须在锁定版本上核验，核验不了的写成接口桩。本文记录会话桥用到的三家官方命令行：
核验了什么、怎么核验的、哪些没能核验。

## 1. 锁定的版本

| 程序 | npm 包与版本 | 用途 |
|---|---|---|
| Codex CLI | `@openai/codex@0.169.0` | `chatgpt.chat`、`codex.chat`、`codex.dev` |
| Claude Code | `@anthropic-ai/claude-code@2.1.323` | `claude.chat`、`claude.dev` |
| CodeBuddy Code | `@tencent-ai/codebuddy-code@2.184.0` | `workbuddy.chat`、`workbuddy.dev` |

版本写死在 `services/llm-bridge/Dockerfile` 和 `.github/workflows/ci-llm-bridge.yml`；CI 的冒烟检查核对容器里的版本。

## 2. Codex：`codex app-server`（JSON-RPC，每行一条）

核验方式：`codex app-server generate-json-schema` 生成协议 schema（109 个客户端方法、86 种通知、10 种服务端请求），
并在本会话里对真实程序逐个调用（未登录；本会话的网络拦截了 OpenAI 的域名）。

| 驱动用到的 | 结果 |
|---|---|
| `initialize` + `initialized` | 实测通过 |
| `account/read` | 实测：未登录时 `account: null, requiresOpenaiAuth: true`；schema：`chatgpt` 账号带 `email`、`planType` |
| `account/login/start`：`chatgptDeviceCode` → `verificationUrl`、`userCode`、`loginId`；`apiKey` | 只核验了 schema；实测时请求发到了 `auth.openai.com`，被本会话的网络拦截 |
| `account/login/completed` 通知、`account/logout` | 只核验了 schema |
| `model/list` | 实测通过（不需要登录）：返回模型、默认模型、可选的推理强度 |
| `thread/start`（`cwd`、`model`、`approvalPolicy: never`、`sandbox`、`developerInstructions`） | 实测通过 |
| `thread/resume` | 只核验了 schema（仿真程序的测试覆盖了“重启后先恢复再发送”） |
| `turn/start`（`input`、`sandboxPolicy`、`approvalPolicy`）、`turn/started`、`item/completed`、`error`（`willRetry`） | 实测：回合开始、用户消息落盘、连不上平台时不断发 `error` 通知并一直重试 |
| `turn/completed`（`completed` / `failed` / `interrupted`，`codexErrorInfo`）、`agentMessage`、`thread/tokenUsage/updated` | 只核验了 schema：没有登录，拿不到真实的回答 |
| `turn/interrupt` | 只核验了 schema |
| `thread/list` | 实测通过：发过消息的线程才会出现 |
| `thread/turns/list`（`itemsView: full`、`sortDirection: asc`、分页） | 实测通过；还没发过消息的线程返回错误 `not materialized yet` 或 `list_turns is not supported yet`，驱动当作空会话 |
| `thread/read` 带 `includeTurns` | schema 里标为不推荐，没有采用 |
| `thread/archive` | 实测通过 |
| 审批请求（`item/commandExecution/requestApproval` 等） | 只核验了 schema；驱动固定 `approvalPolicy: never`，收到请求一律回 `decline` |

没能核验、需要人类登录后在验收任务里实测的：设备码登录的全过程、真实回合的完整事件、用量限制时的错误、
**Codex 的沙箱（bubblewrap）在 Docker 容器里能否工作**（`cap_drop: ALL` 加 `no-new-privileges` 之下可能需要调整）。

`codex cloud`：`exec --env`、`list --json`、`status` 的参数从 `--help` 核验了，但输出格式要登录后才能看到，
所以 `codex.cloud` 是接口桩（返回 `NOT_SUPPORTED` 并说明原因），没有标为完成。

## 3. Claude Code：无界面模式

核验方式：对真实程序实测 `claude -p "你好" --output-format stream-json --verbose --model sonnet --session-id <uuid> --tools ""`。

- 事件逐行输出：`system`（`init`）、`assistant`（`message.content` 是块列表）、`rate_limit_event`、最后一行是结果
  （`usage`、`stop_reason`、`total_cost_usd` 等）；
- 会话记录写在 `$CLAUDE_CONFIG_DIR/projects/<编码后的工作目录>/<会话号>.jsonl`，每行一条，`type` 为 `user` / `assistant` 的行带 `message`；
- `claude auth status` 输出 JSON：`loggedIn`、`authMethod` 等；
- `--help` 核验了驱动用到的参数：`--session-id`、`--resume`、`--model`、`--tools`、`--permission-mode`、`--allowedTools`。

**说明**：这次实测是在本会话的环境里做的，官方程序用上了这个云端会话自带的 Claude 登录，发出了一条“你好”。
这不是有意的：我以为清掉几个环境变量后它处于未登录状态。之后没有再用这份登录做任何调用；
驱动的子进程只继承白名单里的环境变量，带真实密钥的实测用例要显式提供 `BRIDGE_LIVE_CLAUDE_API_KEY` 才会运行，本次没有运行。

**`claude auth status` 的补充核验（T-0229，离线：清空环境变量、不带代理、全新的主目录）**：
- 设了 `ANTHROPIC_API_KEY`（假值）时输出 `loggedIn: true`、`authMethod: api_key`、`apiKeySource: ANTHROPIC_API_KEY`；
  设了 `CLAUDE_CODE_OAUTH_TOKEN`（假值）时是 `authMethod: oauth_token`。输出里没有邮箱，所以用密钥或令牌登录时会话桥不知道是哪个账号。
- **在这个云端会话里，什么凭据都不给它也报告 `loggedIn: true`、`authMethod: oauth_token`**：会话所在的机器在环境变量之外给官方程序
  提供了登录。这是本环境特有的（CI 的新容器里是未登录，冒烟检查核对过），也是上面那次非有意调用的原因。
  所以在这类会话里做本机验收时，会话桥不接 Claude 程序（`BRIDGE_CLAUDE_BIN` 指向不存在的路径）。

没能核验的：`--resume` 续接真实会话、API 密钥与 `claude setup-token` 令牌的真实回合、错误结果的真实格式（驱动按 CodeBuddy 实测到的
`is_error` + `errors` 和文档里的 `result` 两种写法都处理）。

`claude --cloud`：没有非交互的列表和结果接口，`claude.cloud` 是接口桩。

## 4. CodeBuddy Code（WorkBuddy 通道）

核验方式：对真实程序实测 `codebuddy -p "你好" -y --output-format stream-json --session-id cb-test-1 --tools ""`（未登录）。

- 参数和事件格式与 Claude Code 相同，所以两家共用一个驱动（`bridge/drivers/cli_stream.py`）；
- 未登录时输出 `result`：`is_error: true`、`errors: ["Authentication required. Please use /login command ..."]`，驱动归为“未登录”；
- 会话记录在 `$CODEBUDDY_CONFIG_DIR/projects/<目录>/<会话号>.jsonl`，格式与 Claude 不同：`type: message`、`role`、
  `content: [{type: input_text | output_text, text}]`，驱动两种都能读；
- 可选模型列在 `--help` 的 `--model` 说明里，驱动从那里读；
- 没有查询登录状态的子命令：配置了 API 密钥就显示“已配置”，是否有效要到发送时才知道。

没能核验的：`CODEBUDDY_API_KEY` 登录后的真实回合；个人订阅的登录态能否在容器里使用；
WorkBuddy 桌面端的会话与 CodeBuddy 命令行的会话是否是同一份。在核验之前，页面上写明“看到的是 CodeBuddy 命令行的会话”。

## 5. 测试怎么覆盖

| 层 | 做法 |
|---|---|
| 会话、回合、权限、账号一致性 | `tests/test_bridge.py`：经 `/mcp` 和 `/admin` 端到端，用不访问外部服务的 `fake` 平台 |
| 两个驱动的逻辑 | `tests/test_drivers.py`：对 `tests/fakes/` 里仿真的官方程序；仿真程序的消息形状取自第 2 ~ 4 节的 schema 与实测输出 |
| 真实程序 | `tests/test_live.py`：CI 里安装锁定版本后实测不需要登录的部分（Codex 的模型、建线程、读取、归档；CodeBuddy 的模型、未登录错误、会话记录） |
| 镜像 | `infra/llm-bridge/smoke.sh`：非 root、三家版本、健康检查、认证、工具清单、Codex 模型列表、未登录时拒绝新建 |
| 设置页与同机部署 | `tests/e2e/llm_bridge_browser.cjs`（T-0229）：真实后端 + 真实前端 + 真实会话桥，13 步；CI 的 `workspace` job 用 `make up` + `make llm-bridge-up` 起全栈后运行 |

本会话里 Debian 软件源被网络策略拦截，没能在本地构建完整镜像；镜像的构建和冒烟检查由 CI 完成。
浏览器验收在本机跑过（工作台 API 与会话桥用 uvicorn 直接启动、前端用 Vite 开发服务器、真实的 codex 与 codebuddy 程序）。
跑的过程中发现并修了一个部署问题：`make llm-bridge-up` 的 Compose 命令少了 `--env-file .env`，读不到仓库根目录 `.env` 里的令牌。

## 6. 许可证

Codex CLI 是 Apache-2.0。Claude Code 和 CodeBuddy Code 是各自厂商的专有软件，按它们的服务条款使用；
镜像由部署者自己构建、自己使用，本仓库不分发包含它们的镜像。
