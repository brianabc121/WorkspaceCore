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

**模型与推理强度（T-0288，2026-11-17）**。核验方式：从 npm 下载锁定版本的 `@anthropic-ai/claude-code-linux-x64@2.1.323`，
只解包、不运行，在二进制里读帮助文本和内置配置。

- `--model <model>` 的帮助原文：“Model for the current session. Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's full name.”
  也就是说，别名和完整模型名都可以填。
- `--effort <level>` 的帮助原文：“Effort level for the current session”，可选值 `low, medium, high, xhigh, max`。
  - 不认识的值会打印警告 “Unknown --effort value … ignoring it and using the default effort”，然后照常运行；
  - 模型不支持所选的强度时，官方程序会静默降级（会话状态里的说明是 “after any silent downgrade for the selected model”）。
- 模型菜单取自内置的 `model_selector_config` 里 `id: "cc"` 那一份：
  - 常用（`section: main`）：`claude-opus-5-5`（Opus 5.5）、`claude-sonnet-5-5`（Sonnet 5.5）、`claude-fable-5-2`（Fable 5.2）、`claude-haiku-5-5`（Haiku 5.5），`effort_levels` 都是 low 到 max，默认 medium。
  - 更多（`section: overflow`）：Fable 5.1、Opus 5、Sonnet 5、Fable 5、Opus 4.8、Opus 4.7 都是 low 到 max；Opus 4.6、Sonnet 4.6 没有 xhigh；
    Haiku 4.5（`claude-haiku-4-5-20251001`）、Opus 4.1（`claude-opus-4-1-20250805`）是 `thinking: none`，不支持推理强度。
- 驱动的做法（`bridge/drivers/cli_stream.py` 的 `CLAUDE_MODELS`）：
  - 页面上先列四个别名（sonnet、opus、fable、haiku），再列上面这些完整模型名，各自带可选的强度；
  - 选了推理强度就加 `--effort <level>`；
  - 管理器新建会话时检查强度是不是所选模型支持的。
- CodeBuddy Code 没有核对推理强度的参数，带了也不传。

没能核验的：用真实账号跑带 `--effort` 的回合（本会话不调用真实的 Claude 登录，原因见上面的说明）；
账号能不能用某个模型由平台决定，用不了时官方程序会报错，错误照常显示在回合里。

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

### 4.1 长时间任务用到的事件和开关（T-0291、T-0292，2026-11-18）

方案见 `docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md` 第 3 节。核验方式和 T-0288 一样：锁定版本的程序文件只解包、不运行，读里面的事件名、字段名和环境变量名；Codex 用 `codex app-server generate-json-schema` 生成的协议。**都没有跑真实回合。**

| 用到的 | 来源 | 驱动怎么用 |
|---|---|---|
| Claude Code 2.1.323、CodeBuddy Code 2.184.0 的 stream-json `system` 事件 `task_started` / `task_progress` / `task_updated`（带 `patch`）/ `task_notification`：`task_id`、`status`、`description`、`summary`、`task_type`、`last_tool_name` | 程序文件 | 记成回合进度里的“官方程序的后台任务”（`bridge/drivers/cli_stream.py` `_task_event`）；回合结束时还有没完成的，记一条提示 |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`、`CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS` | 程序文件；Claude 的含义另见官方文档 | 只在托管作业能用的会话里设成 `1`，并用 `--append-system-prompt` 告诉模型用 `llm-task` |
| Claude `--append-system-prompt` | `--help` | 同上 |
| Codex 0.169.0 的 `item/started`（`commandExecution` 带 `command`）、`item/commandExecution/outputDelta`（`itemId`、`delta`）、`item/completed` | 生成的协议 | 回合进度里的“正在跑的命令”和输出末尾，只在内存里 |
| Codex `developerInstructions` | 生成的协议（第 2 节已用） | 开发会话能开作业时加上 `llm-task` 的说明 |

没能核验的：这几种事件在真实回合里的顺序和频率；Claude `-p` 回答结束后等后台命令的实际时限；CodeBuddy 回答结束时怎样处理后台任务；Codex 的 `workspace-write` 沙箱没打开联网时，`llm-task` 连不上管理器的本机地址（按 Codex 文档推断，`llm-task` 的报错写明了这种情况）。部署后在真实账号上验证，见方案 12.4。

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
