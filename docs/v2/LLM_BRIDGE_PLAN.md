# 大模型会话桥方案（T-0226）：一个 Docker 部署的 MCP 服务，让多个 OpenClaw 向 ChatGPT / Codex / Claude / WorkBuddy 发指令

> 状态：**已批准，实施中**。人类 2026-11-13 看过草案后要求“根据这份设计方案全面落实这个 mcp 工具”，没有对 D1 ~ D9 另作指示，
> 所以都按第 14 节的推荐执行。实施任务从 T-0227 起（第 15 节）；实施中确定的细节和与草案不同的地方记在第 18 节。
> 在锁定版本上的核验结果见 `docs/decisions/2026-11-13-llm-bridge-verification.md`。
>
> 核验说明：本会话的网络只放行了部分站点。下文标【已核验】的内容来自打开的官方文档、官方仓库源码或 npm 包内自带文档；
> 标【未核验】的只来自搜索摘要，实施前必须在锁定版本上实测（第 15 节第一个任务），核验不了的按项目规则写成接口桩，不标完成。
>
> **需求原文**（人类 2026-11-13）：
> 给我新增一个分支用于开发一个 chatgpt/codex/claude/workbuddy 的 mcp 工具。在 docker 上部署，由 openclaw 通过这个 mcp 工具给各个大模型发送指令。
> 这个 mcp 需求功能如下：
> 1. 可以处理多个 openclaw 发送的指令：如 openclaw 1 号通过 mcp 工具向 chatgpt 聊天模式发送消息“你好”；openclaw 1 号通过 mcp 工具向 chatgpt 聊天模式发送消息“大家好”；
>    openclaw 2 号通过 mcp 工具向 codex 发送“你好”。一个 mcp 工具需要具备被多个智能体调用，并管理多个会话的能力（会话可以跨多个不同的模型）。
> 2. 能够调取之前的会话列表。openclaw 可以通过 mcp 工具查看当前 codex/claude/chatgpt/workbuddy 账号下的会话列表。
> 3. 能够在新建会话的时候，选择模型，如开发模式下还能选择配置环境。
> 4. 一个 mcp 仅仅可以管理 claude/chatgpt/codex/workbuddy 等大模型的一个账号。不能出现一个模型平台两个账号的情况。
> 5. 如果未来用户需要管理多个账号的情况，则需要部署多个 docker 容器并安装多个 mcp 工具进行管理。
> 根据以上需求先调研 github 上的同类开源方案，想一想业内专家会怎么做，再写一份设计方案。

## 0. 一页看懂

```
OpenClaw 1 号 / 2 号 / 3 号（各自的服务器；OpenClaw 本身仍然不开 exec）
   │  MCP 工具：llm_status / llm_models / llm_environments / llm_session_create / llm_send /
   │            llm_turn_get / llm_turn_cancel / llm_session_list / llm_session_get / llm_session_archive
   ▼
工作台（推荐，D1）：认出是哪个实例、按实例的开关放行、限额、执行记录、审计，再转给会话桥
   ▼
会话桥容器 llm-bridge（一个容器 = 每个平台最多一个账号）
   ├─ MCP 服务（streamable-http，Bearer 令牌）
   ├─ 会话与回合：登记表、每个会话一条串行队列、每个平台的并发上限、事件记录（SQLite，放数据卷）
   ├─ 平台驱动（各驱动一个官方的无界面入口）
   │    codex     → codex app-server（JSON-RPC）            本地开发 / 聊天；codex cloud 命令行 → 云端任务
   │    chatgpt   → 没有官方接口，见第 6.2 节（推荐用同一个 ChatGPT 登录下的 Codex 作聊天通道）
   │    claude    → 官方 claude 程序（Agent SDK / stream-json）  本地开发 / 聊天；claude --cloud → 云端会话（能力有限）
   │    workbuddy → CodeBuddy Code 命令行（ACP 或 --serve）
   └─ 数据卷 /data：每个平台一个登录目录（只放一个账号）、会话记录、工作目录
多账号：再起一个容器（自己的数据卷、自己的地址和令牌），在工作台里再登记一座桥。
```

三件事要先讲清楚，它们决定了方案的形状：

1. **ChatGPT 网页聊天和 claude.ai 网页聊天都没有官方接口**。能列出、续写的只有各家“编程智能体”这一侧的会话（Codex、Claude Code、CodeBuddy）。
   要操作 chatgpt.com 里的对话只能靠自动化网页，违反条款、有封号风险、页面一改就坏，本方案不做（第 6.2 节给出替代）。
2. **“账号下的会话列表”读的是这个容器里的记录加上云端任务**，不包含你在自己电脑、手机上开的会话（Codex、Claude Code、CodeBuddy 的本地会话都存在各自机器的磁盘上）。
3. **Claude 订阅账号放进自动化工具有合规限制**（第 6.3 节），用订阅还是 API 密钥需要人类决定（D3）。

## 1. 目标与边界

要做的：

- 一个 MCP 服务，多个 OpenClaw 同时调用；每个调用方可以同时有多个会话，会话可以分布在不同平台、不同模型上。
- 新建会话时选平台、模式（聊天 / 开发 / 云端）、模型；开发模式下选环境。
- 向已有会话续发消息，取结果，中途取消。
- 查看这个账号下的会话列表（范围见上面第 2 点）。
- 一座桥每个平台只有一个账号；多账号靠多部署。

不做的：

- 不自动化 chatgpt.com、claude.ai 网页，不用逆向出来的接口。
- 不实现各家的登录协议，不读取、不转发各家的登录令牌：登录由官方程序自己完成，凭据留在它自己的目录里。
- 不让 OpenClaw 服务器开 exec 或挂 Docker socket；命令只在会话桥容器（或它指向的运行环境）里由各家的智能体执行。
- 不做模型网关（按 token 转发的 OpenAI 兼容接口）。OpenClaw 自己的模型密钥仍按 `docs/v2/OPENCLAW_PLAN.md` 走工作台。

## 2. 调研：GitHub 上的同类开源方案

**结论：没有一个项目同时覆盖四个平台加“单账号、多调用方、多会话”。** 各项目都只解决其中一段。
下表的仓库页面都打开过（星数、许可证、归档状态来自页面）；最后提交日期没能核验（GitHub API 在本会话返回 403）。

| 项目 | 做法 | 会话能力 | 多调用方 | 登录 | 状态 / 许可证 |
|---|---|---|---|---|---|
| BeehiveInnovations/pal-mcp-server（原 zen，11.8k 星） | 以各家 API 密钥为主；`clink` 桥接 Gemini / Codex / Claude Code 等命令行 | 跨工具续接上下文；不能列账号历史会话 | 没说明 | API 密钥；命令行用自己的登录 | 活跃，有 Dockerfile；Apache-2.0 |
| steipete/claude-code-mcp（1.3k） | 每次请求起一个 `claude` 进程，单个工具 | 传 `sessionId` 续接；没有列表、不能选模型 | 没说明 | 命令行订阅登录 | **2026-05-15 已归档**；MIT |
| tuannvm/codex-mcp-server（649） | 包装 Codex 命令行 | `sessionId`、`listSessions`（只限本进程）、`model`、推理强度 | 没说明 | 文档只写 API 密钥 | 活跃；ISC |
| mkXultra/ai-cli-mcp（27） | 后台子进程跑 Claude / Codex / OpenCode 等 | `run`（可续接、可选模型）、`list_processes`、`get_result`、`peek`、`kill_process` | 立即返回进程号，可并行 | 各命令行自己的登录 | 活跃；MIT |
| coder/agentapi（1.5k） | 用内存终端解析各家的终端界面，对外提供 HTTP + SSE | 一个服务一个智能体 | 没说明 | 无鉴权 | **已弃用并归档**；MIT |
| agentclientprotocol/claude-agent-acp（2.8k，原 zed-industries/claude-code-acp） | 官方 Claude Agent SDK → ACP | 新建、列表、恢复、分叉、删除、关闭；模型走配置项 | 一条连接多个会话 | Claude 登录 | 活跃；Apache-2.0 |
| agentclientprotocol/codex-acp（573） | 启动 Codex app-server 并翻译成 ACP | 载入并回放；可选模型、推理强度、审批、沙箱 | 有子会话 | ChatGPT 登录或 API 密钥 | 活跃；Apache-2.0 |
| openai/codex 自带的 app-server | JSON-RPC（stdio / unix / WebSocket） | `thread/start`、`list`、`resume`、`fork`、`archive`、`model/list` | 多客户端订阅 | ChatGPT 登录或 API 密钥 | 官方；多处标 experimental |
| openclaw/acpx（3.4k） | 无界面的 ACP 客户端，内置 30 个智能体的启动配置（含 Codex、Claude Code；不含 CodeBuddy，但可以自定义命令接入） | 命名的持久会话、一次性运行、导入导出 | 同一会话的后续消息排队，不同会话并行 | 各智能体自己的登录 | 活跃，未到 1.0；MIT |
| Oortonaut/mcacp（12） | **MCP 服务 → ACP 智能体**（两端都是 stdio） | `new_session`、`load_session`、`list_sessions`、`close_session`、`prompt_start`、`prompt_events`；不能选模型 | 可并行，没有 HTTP | 各智能体自己的登录 | 很早期；Apache-2.0 |
| slopus/happy（24.6k）、siteboon/claudecodeui（14.6k） | 手机端 / 网页界面包装 Claude Code、Codex | 后者自动发现 `~/.claude` 里的历史会话并续接 | 面向人，不面向智能体 | 命令行登录 | 活跃；MIT / AGPL-3.0 |
| smtg-ai/claude-squad（8.8k）、kbwo/ccmanager（1.3k） | tmux / 伪终端加 git worktree 的终端界面 | 只管进程，没有接口 | — | 命令行登录 | 活跃；AGPL-3.0 / MIT |
| megamen32/chatgpt-cdp-mcp（0 星）等 ChatGPT 网页桥 | 经浏览器调试接口操作一个已登录的 chatgpt.com 页面 | `list_chats`、`new_chat`、`send_message` | 一个进程一个页面 | 用户自己的浏览器登录 | 都是个人小项目；条款与封号风险高 |
| 腾讯 CodeBuddy Code（闭源命令行） | `-p` 无界面、`--acp`、`--serve`（REST，Beta）、Agent SDK | 恢复、继续；`--serve` 有会话列表接口 | 未核验 | 交互登录或 `CODEBUDDY_API_KEY` | 官方；专有 |

从调研得到的判断：

- **方向最接近的是“ACP 客户端上面包一层 MCP”**（acpx + mcacp 的组合）。mcacp 的工具划分（开始发送和取事件分开）值得参考，但它太早期、只有 stdio、不能选模型，不适合直接依赖。
- **各家都已经有官方的无界面入口**，不需要解析终端界面（agentapi 那条路已经被上游放弃）。
- **所有项目都没解决、需要我们自己做的**：多个调用方共用一个账号时会话归谁、谁能看；开发模式的环境选择；跨平台统一的会话列表；带鉴权的网络化 MCP；限额与审计；“一个平台只能一个账号”的硬约束。
- 已归档或停运的（agentapi、claude-code-mcp、vibe-kanban、crystal、claude-code-webui）不复用；AGPL 的（claudecodeui、claude-squad）只参考做法。
- OpenClaw 自带把任务委派给 ACP 智能体的能力（`sessions_spawn` 加 acpx）【未核验】，但它要求在 OpenClaw 主机上放凭据并起子进程，和本项目“OpenClaw 不开 exec、只开工作台工具”的规则冲突，所以仍然需要独立的桥容器。

## 3. 业内专家会怎么做

把这件事交给做过智能体平台的人，通常会坚持这几条：

1. **只走官方的可编程入口**。Codex 用 app-server，Claude 用 Agent SDK 或官方程序的 stream-json，CodeBuddy 用 ACP 或 `--serve`。
   不解析终端输出、不驱动网页、不逆向接口：这三种都会在上游改版时坏掉，后两种还有账号风险。
2. **控制面加驱动**。上层是统一的会话、回合、事件模型和一套工具；下层每个平台一个驱动，驱动之间互不知道。
   行业正在向 ACP（Agent Client Protocol）收拢，Codex、Claude Code、CodeBuddy 都有 ACP 入口，所以驱动接口按 ACP 的形状设计（新建 / 载入 / 列表 / 发送 / 取消 / 事件流）。
   但不强求每个驱动都经 ACP：Codex 的 app-server 原生就有会话列表和模型列表，直接用更少一层。
3. **长任务用“提交、再取结果”的方式**。一次开发回合可能跑几十分钟，超过 MCP 调用方的超时。
   所以发送立即返回回合号，可以带一个短等待时间；之后用回合号取状态和结果。每次发送带调用方给的幂等号，重试不会重复发送。
4. **一个会话同一时刻只有一个回合**。同一会话的消息按到达顺序排队；不同会话并行，但每个平台有并发上限，因为订阅账号有速率和用量限制。
5. **凭据按账号隔离，登录交给官方程序**。每个平台一个独立的登录目录，放在数据卷里；桥只知道“已登录 / 未登录 / 是哪个账号”，不接触令牌内容。
6. **身份在网关上确定，不相信调用方自报**。哪个 OpenClaw 在调用，由令牌决定，不由工具参数决定。
7. **状态落盘**。会话登记、回合状态、事件都持久化；容器重启后，运行中的回合标成“结果未知”，由调用方决定重发还是查看会话。
8. **锁版本、做契约测试**。app-server、Codex cloud、CodeBuddy `--serve` 都标着 experimental 或 Beta，升级前先跑一遍对锁定版本的实测。

本方案按这几条设计。

## 4. 总体设计

### 4.1 组成

| 组成 | 位置 | 说明 |
|---|---|---|
| 会话桥服务 | 新目录 `services/llm-bridge/`（Python 3.12，D8） | MCP 服务、会话与回合、驱动、管理接口。镜像里另装 Node 22 和三家的官方命令行（版本锁定，写进 `docs/versions.md`） |
| 部署文件 | `infra/llm-bridge/`（Compose，可选部署） | 一个容器、一个数据卷；不进核心 Compose |
| 工作台模块 | `services/api/app/modules/llm_bridge` + `apps/web/src/modules/llm_bridge`（按 `docs/modules/README.md` 的插拔式模块做） | 登记会话桥、按实例的权限、把工具转给桥、登录与环境的设置页面 |

### 4.2 OpenClaw 怎么调用（D1）

两种接法，推荐第一种：

| | 甲：经工作台（推荐） | 乙：OpenClaw 直接连会话桥 |
|---|---|---|
| 做法 | 工具加在工作台已有的 `/mcp` 里；工作台按实例凭证认出调用方，检查后转给桥 | 在每个 OpenClaw 的 `mcp.servers` 里再加一个服务，各用一枚桥的调用方令牌 |
| 调用方身份 | 现成的实例凭证，不用另发令牌 | OpenClaw 的请求头是按服务配置的【已核验】，一个实例一枚令牌，要另外发放和保管 |
| 权限、限额、执行记录、审计 | 沿用工作台现有的 | 桥自己再做一套 |
| 外网模式 | 沿用实例到工作台的现有通道 | 要再给桥开一条通道；OpenClaw 会拦回环地址（T-0133 遇到过） |
| 设置 | 都在工作台页面上 | 要改每台 OpenClaw 的配置 |
| 与现有规则 | 仍然“只开工作台工具”，需要新增一条例外（第 13 节） | 要放宽“只开工作台工具和 IM 发消息工具”，改动更大 |
| 代价 | 多一跳；工作台停了就用不了 | 桥可以脱离工作台单独使用 |

会话桥本身始终是一个标准的 MCP 服务，两种接法用的是同一套工具。选甲时，工作台是它唯一的调用方，并在每次调用里带上“代表哪个实例”；选乙时，令牌直接对应实例。

### 4.3 名词

| 名词 | 含义 |
|---|---|
| 调用方 | 一个 OpenClaw 实例（由令牌确定） |
| 平台 | `chatgpt` / `codex` / `claude` / `workbuddy` |
| 模式 | `chat`（聊天：不动文件、不执行命令）/ `dev`（开发：在选定环境里读写文件、执行命令）/ `cloud`（云端：任务在厂商的云环境里跑） |
| 通道 | 平台 + 模式，例如 `codex.dev`、`claude.chat`。每个通道由一个驱动实现，能力不同（第 6 节） |
| 环境 | 开发 / 云端模式下会话在哪里干活（第 7 节） |
| 会话 | 桥里的一条记录（`bs_…`），对应平台上的一个原生会话 |
| 回合 | 向会话发一次消息到它答完的过程（`bt_…`） |

## 5. 会话与回合

### 5.1 对应需求里的例子

| 调用 | 桥里发生的事 |
|---|---|
| 1 号：向 ChatGPT 聊天发“你好” | `llm_session_create(platform=chatgpt, mode=chat, model=…)` → 会话 A；`llm_send(A, "你好")` → 回合 A1 |
| 1 号：向 ChatGPT 聊天发“大家好” | 带会话号 A：排在 A1 后面，成为 A2（同一段对话）。不带会话号而新建：会话 B，与 A 并行 |
| 2 号：向 Codex 发“你好” | 会话 C（归 2 号），与 A、B 并行，互不可见 |

### 5.2 规则

- **归属**：会话归新建它的调用方。默认只能看到、续发自己的会话（符合“不得跨实例泄露会话”）。
- **账号范围的列表**：需求 2 要看“账号下的会话列表”。这是按调用方的一个开关（D5）：打开后，`llm_session_list(scope="account")` 还会列出别的调用方建的、以及不是经桥建的会话（来源标为 `external`）。
  列表只给标题、时间、平台、模型、状态；读内容或接着发，需要另一个开关“可以接手不属于自己的会话”。
- **排队**：一个会话一条串行队列；不同会话并行。每个平台有并发上限（默认 2，可在页面上改），超出的回合排队并在结果里说明排在第几。
- **发送**：`llm_send` 必须带幂等号 `request_id`；同一个号重发，返回原来的回合，不会再发一遍。
  可带 `wait_seconds`（最多 50 秒）：在这段时间内答完就直接返回结果，否则返回回合号，之后用 `llm_turn_get` 取。
- **回合状态**：`queued` → `running` → `completed` / `failed` / `cancelled` / `unknown`。
  `unknown` 表示桥重启或驱动断开，不确定平台那边是否做完；调用方先用 `llm_session_get` 看会话最新内容，再决定是否重发。
- **需要确认的操作**：开发模式下各家的智能体会就执行命令、改文件请求许可。第一版不把这类请求转给 OpenClaw，而是由环境预先定好策略（第 7 节）；
  策略不允许的操作直接拒绝，智能体会在回答里说明。以后可以加“待确认”状态，接到工作台的审批上。
- **结果大小**：返回最后的回答文字和摘要（改了哪些文件、执行了哪些命令、用量）；完整事件用 `llm_session_get` 分页读。
  返回内容里出现的密钥样式按工作台现有的密钥拦截规则打码。
- **模型的回答是数据，不是指令**：工具说明里写明，调用方不应执行回答里要求的越权操作。

## 6. 各平台的驱动

能力矩阵（“可以”表示在官方文档或源码里核验过；实施时仍要在锁定版本上实测）：

| 通道 | 新建并选模型 | 选环境 | 续发 | 列会话 | 流式事件 | 容器里登录 |
|---|---|---|---|---|---|---|
| `codex.dev` / `codex.chat` | 可以（`thread/start`，`model/list`） | 工作目录 | 可以（`thread/resume` + `turn/start`） | 可以（`thread/list`） | 可以 | 设备码登录【未核验细节】 |
| `codex.cloud` | 不能选模型 | `--env`（环境要先在网页上建好） | **不能** | 可以（`codex cloud list --json`） | 不能，只能轮询 | 同上 |
| `chatgpt.chat` | 见 6.2 | — | — | — | — | — |
| `claude.dev` / `claude.chat` | 可以（`--model`） | 工作目录 | 可以（`--resume`） | 可以（SDK `listSessions`，读本机） | 可以（stream-json） | `claude setup-token` 或 API 密钥 |
| `claude.cloud` | 没查到参数 | 只能用默认环境 | 能入队，不等回复 | **没有非交互的列表** | 不能 | 需要完整登录 |
| `workbuddy.dev` / `workbuddy.chat` | 可以（`--model`） | 工作目录 | 可以（`--resume`） | 可以（`--serve` 的会话列表接口，Beta） | 可以 | `CODEBUDDY_API_KEY`；订阅登录态能否在容器里用【未核验】 |

### 6.1 Codex

- 驱动常驻一个 `codex app-server` 进程，用 JSON-RPC 对话【已核验：源码里有 `thread/start|resume|fork|list|read|archive`、`turn/start`、`model/list`、`account/read`、`account/login/*`】。
  不包装 `codex exec`：它没有会话列表和模型列表。
- `codex.chat`：同一个驱动，会话用只读沙箱、空的工作目录、不联网，当作纯聊天。
- `codex.cloud`：调用 `codex cloud exec --env <环境>` 和 `codex cloud list --json`【已核验，标 EXPERIMENTAL】。云端任务不能续发、不能选模型，
  所以这个通道的 `llm_send` 只允许每个会话一个回合，结果靠轮询任务状态取。
- 登录目录：`CODEX_HOME=/data/home/codex`。一个目录被多个进程同时使用是否安全没有官方说明，所以只起一个 app-server，所有会话都经它。

### 6.2 ChatGPT 聊天

chatgpt.com 的对话没有任何官方接口可以新建、续写或列出【已核验没有；条款原文未核验，搜索摘要显示禁止用程序自动提取输出】。三条路：

| 做法 | 是否同一个账号 | 会话是否出现在 chatgpt.com 里 | 风险 |
|---|---|---|---|
| 甲（推荐）：`chatgpt.chat` 用同一个 ChatGPT 登录下的 Codex，以纯聊天方式运行（即 `codex.chat`） | 是，同一个订阅 | 否，出现在 Codex 的会话列表里 | 低；可选模型是 Codex 提供的那些 |
| 乙：用 OpenAI API 密钥（Responses / Conversations 接口） | 否，API 是单独计费的账号 | 否 | 低；按量付费 |
| 丙：自动化 chatgpt.com 网页 | 是 | 是 | 高：违反条款、可能封号、页面改版就坏 |

推荐甲，乙作为可选驱动，丙不做（D2）。也就是说：**“查看 ChatGPT 账号下的会话列表”在合规范围内做不到**，只能看到经桥建立的聊天会话。

### 6.3 Claude

- `claude.dev` / `claude.chat`：驱动官方的 `claude` 程序（经 Claude Agent SDK，或直接用 `-p --input-format stream-json --output-format stream-json`）【已核验】。
  会话记录在 `CLAUDE_CONFIG_DIR=/data/home/claude` 下，列表用 SDK 的 `listSessions()` / `getSessionMessages()`。
  `claude.chat` 关掉全部工具，只对话。
- `claude.cloud`：`claude --cloud "任务"` 能建云端会话，`claude -p "消息" --cloud <会话号>` 能入队一条消息【已核验】；
  但**没有非交互的列表、没有结果流、不能按调用指定云环境**。第一版只做成接口桩并标明限制，不标完成。
- claude.ai 网页聊天没有官方接口，不做。
- **合规**（Anthropic 的法律与合规说明，【已核验】）：订阅登录只面向 Claude Code 和官方应用的日常使用；开发者（包括用 Agent SDK 的）应使用 API 密钥；
  不得替用户收集、保存或转发 claude.ai 凭据。用户本人在未修改的官方程序里用自己的订阅登录是允许的。
  所以：
  - 用 **API 密钥**（Claude 控制台账号）最稳妥，按量付费；
  - 用**订阅登录**时，桥只启动未修改的官方程序，由用户本人完成登录，桥不读令牌。这属于自用范围，但把它放在长期运行的自动化里处在灰区，
    上游政策一变就可能受限。另有报道称 2026 年年中起无界面用法改走单独的额度【未核验】。
  - 用哪种由人类决定（D3）；两种驱动的代码相同，只是登录方式不同。

### 6.4 WorkBuddy

- WorkBuddy 是腾讯的桌面办公智能体，和 CodeBuddy 共用一套智能体架构【新闻来源，未核验】；没有查到它单独的公开接口。
- 可编程的是 **CodeBuddy Code 命令行**：`-p -y` 无界面、`--acp`、`--serve`（REST，含会话列表，Beta）、Agent SDK【已核验：npm 包自带文档】。
- 所以 `workbuddy.*` 通道由 CodeBuddy Code 驱动。还不确定的两点，放在核验任务里：
  1. WorkBuddy 桌面端的会话和 CodeBuddy 命令行的会话是不是同一份；
  2. 个人订阅的登录态能否在容器里使用，还是只能用 `CODEBUDDY_API_KEY`。
  核验不了就先只支持 API 密钥，并在页面上写明“看到的是 CodeBuddy 命令行的会话”（D4）。

### 6.5 驱动接口

每个驱动实现同一组操作，形状参照 ACP：`status()`（是否登录、账号标识）、`models()`、`create(model, environment, options)`、`send(session, text)`（产出事件流）、
`cancel(turn)`、`list(cursor)`、`read(session, cursor)`、`archive(session)`，以及一份能力声明（哪些操作不支持）。
不支持的操作返回明确的错误（例如 `not_supported: codex.cloud 不能续发消息`），不假装成功。
驱动对外部进程和 HTTP 的调用都有超时、错误归类（未登录 / 限额 / 平台故障 / 参数错误 / 未知）和重试上限。

## 7. 环境（开发模式）

环境回答“这个会话在哪里干活、能干什么”。由管理员在工作台页面上维护，智能体只能从列表里选（`llm_environments`）。

| 通道 | 环境是什么 | 包含 |
|---|---|---|
| `*.dev`（本地开发） | 桥上的一份**工作区配置** | 仓库地址与分支（可空）、工作目录、沙箱档位（只读 / 可写工作区）、能否联网、可注入的密钥名、单回合时限 |
| `codex.cloud` | Codex 云端环境的编号或名称 | 环境本身在 ChatGPT 网页上建；页面上登记它的编号（没有非交互的列表命令） |
| `claude.cloud` | 只有账号的默认云环境 | 不能按调用选择；做成桩 |

- 每个开发会话有自己的工作目录（`/data/work/<会话号>/`，仓库按环境配置克隆进去），会话之间互不可见；归档会话时删除目录。
- 许可策略由环境决定并在建会话时固定：例如“可写工作区、不联网、不询问”。不提供“完全放开”的档位。
- 更重的任务（要装系统软件、跑容器）不在桥里做，交给运行服务器（`docs/v2/RUNNER_PLAN.md`）。以后可以让环境指向运行服务器上的容器，第一版不做。

## 8. 一座桥、一个账号

- 配置里每个平台只有一个账号槽位（是一个对象，不是列表），数据卷里每个平台只有一个登录目录。
- 第一次登录成功后，桥记下账号标识（Codex 用 `account/read`；Claude、CodeBuddy 用各自的状态命令，取不到就记登录时间和令牌指纹的哈希）。
  之后如果登录目录里换成了另一个账号，桥把该平台置为“账号不一致”，拒绝所有调用，直到管理员在页面上明确“更换账号”
  （更换会归档这个平台的全部会话登记，因为旧会话不属于新账号）。
- 多账号：再部署一个容器，用自己的数据卷、地址和令牌，在工作台里登记成另一座桥（例如“会话桥 甲”“会话桥 乙”）。
  选了 D1 甲时，工具多一个可选参数 `bridge`；只有一座桥时不用填。
- 同一个账号不要登记在两座桥上：两边各自并发，会互相挤占限额，会话列表也会各看到一半。页面上按账号标识提示重复。

## 9. 工具（MCP）

| 工具 | 作用 | 主要参数 |
|---|---|---|
| `llm_status` | 各平台是否可用、登录状态、并发与排队情况 | — |
| `llm_models` | 某个通道可选的模型 | `platform`、`mode` |
| `llm_environments` | 某个通道可选的环境 | `platform`、`mode` |
| `llm_session_create` | 新建会话 | `platform`、`mode`、`model`、`environment`（开发 / 云端必填）、`title` |
| `llm_send` | 向会话发消息 | `session_id`、`text`、`request_id`、`wait_seconds` |
| `llm_turn_get` | 取回合的状态和结果 | `turn_id`、`wait_seconds` |
| `llm_turn_cancel` | 取消排队或进行中的回合 | `turn_id` |
| `llm_session_list` | 会话列表 | `scope`（`mine` / `account`）、`platform`、分页 |
| `llm_session_get` | 会话信息与消息记录 | `session_id`、分页 |
| `llm_session_archive` | 归档会话（释放工作目录） | `session_id` |

- 读工具（`llm_status`、`llm_models`、`llm_environments`、`llm_session_list`、`llm_session_get`、`llm_turn_get`）和写工具分开授权。
- 没打开权限的实例看不到也调用不了这些工具（和现有按实例开关的做法一致）。
- 所有工具的结果都带 `platform`、`mode`、`model`，调用方不用自己记。

## 10. 部署

- `infra/llm-bridge/compose.yaml`：一个服务、一个命名数据卷 `/data`；镜像和里面的命令行版本都锁定，不用浮动标签。
- 容器以普通用户运行，不用特权模式，不挂 Docker socket 和宿主目录，设内存、CPU、进程数上限。
- 只监听私网地址（或经现有的 Caddy 反代加 TLS）；MCP 入口 `/mcp` 用 Bearer 令牌；另有只给工作台用的管理接口（登录、环境、并发设置）。
- 健康检查：服务存活、各驱动进程存活、各平台登录状态。
- **登录在工作台页面上完成**（人类 2026-11-06 的要求：设置放在页面上）：
  页面点“登录 Codex” → 桥启动官方程序的设备码登录 → 页面显示网址和验证码 → 用户在自己的浏览器里完成。
  Claude 的 `setup-token` 需要把网页给出的代码贴回来，页面上给一个输入框。API 密钥类的直接在页面上填，经工作台现有的密钥保管下发。
  这些流程的细节都在核验任务里实测；页面走不通的平台，部署指南里写 `docker exec` 的备用步骤。
- 备份：数据卷包含登录凭据，备份要加密；`make backup` 是否纳入由 D9 决定。

## 11. 数据

会话桥（SQLite，放在数据卷里，单容器单写者）：

| 表 | 内容 |
|---|---|
| `accounts` | 每个平台一行：账号标识、登录状态、登录方式、首次登录时间 |
| `environments` | 通道、名称、工作区配置或云端环境编号、许可策略 |
| `sessions` | 桥会话号、平台原生会话号、通道、模型、环境、归属调用方、来源（`bridge` / `external`）、标题、状态、时间 |
| `turns` | 回合号、会话、幂等号、状态、排队与起止时间、用量、错误归类 |
| `events` | 回合的事件流（追加写，按会话分页） |

工作台（新迁移，全部新增，带工作空间的复合外键）：会话桥登记（地址、令牌的密文、名称、状态）、按实例的权限（读、写、账号范围列表、接手他人会话、可用的平台和环境）。
执行记录、审计、通知沿用现有的表。

## 12. 安全

| 风险 | 做法 |
|---|---|
| 一个实例看到另一个实例的会话 | 默认按调用方隔离；账号范围列表和接手会话是单独的开关，默认关 |
| 凭据泄露 | 登录目录只在数据卷里，权限 0700；桥不读取、不记录、不返回令牌；日志和工具结果按密钥样式打码 |
| 模型回答里夹带指令（提示注入） | 回答作为数据返回；开发会话的许可策略固定在环境上，回答改不了它；OpenClaw 这边的工具限制不变 |
| 开发会话在桥容器里执行命令 | 普通用户、无特权、无 Docker socket、每个会话独立目录、默认不联网、不能访问私有网段和工作台；重任务转运行服务器 |
| 刷用量 | 每实例每小时的发送次数上限、每平台并发上限、单回合时限；超限的调用被拒绝并通知管理员 |
| 账号被平台限制 | 只用官方入口；不做网页自动化；Claude 的登录方式由人类按 6.3 的说明选择 |
| 上游接口变化（experimental / Beta） | 版本锁定；升级前跑契约测试；驱动按能力声明降级，不影响其他平台 |
| 桥停机 | 工具返回明确的“会话桥不可用”；工作台其他功能不受影响（第三方故障可降级） |

## 13. 与现有规则的关系

- `docs/AGENTS.md` 第 8 节“不得放宽 OpenClaw 智能体的工具限制（只开工作台工具和 IM 发消息工具）”：选 D1 甲时不变，工具仍然由工作台提供。
- “写操作必须经工作台审批”：`llm_session_create` / `llm_send` / `llm_turn_cancel` / `llm_session_archive` 有副作用（消耗订阅用量、在开发环境里改文件）。
  参照例外八新增了例外九（人类 2026-11-13 批准；生效的写法以 `docs/AGENTS.md` 第 8 节为准，工具名是 `llm_bridge_*`）。下面是草案当时的建议写法：
  > 例外九：管理员按实例打开“大模型会话权限”（并选定可用的平台与环境）后，智能体经工作台向会话桥上的 ChatGPT / Codex / Claude / WorkBuddy 新建会话、发送消息、取消回合、归档会话（`llm_*` 工具），直接执行、不审批；没打开的实例看不到也调用不了。它们仍必须经过权限范围、角色、会话归属、限额、密钥拦截、执行记录、审计与通知管理员的保护，不得绕过；智能体拿不到各平台的登录凭据；开发会话只能使用管理员定好的环境与许可策略；OpenClaw 服务器本身仍然不开 exec、不挂 Docker socket。
- “禁止发明第三方 API”：本方案里所有【未核验】的点都列进了核验任务；核验前对应的驱动只能是接口桩。
- 新功能按插拔式模块做：工作台一侧用 `make module-new` 生成 `llm_bridge` 模块；会话桥服务本身是独立部署的服务，不是工作台模块。

## 14. 决定（人类 2026-11-13：按推荐执行）

| 编号 | 事项 | 推荐 |
|---|---|---|
| D1 | OpenClaw 怎么调用会话桥 | 甲：经工作台（第 4.2 节） |
| D2 | ChatGPT 聊天怎么实现 | 甲：用同一个 ChatGPT 登录下的 Codex 作聊天通道；API 密钥作可选；不做网页自动化（第 6.2 节） |
| D3 | Claude 用订阅登录还是 API 密钥 | 两种都支持，默认 API 密钥；订阅登录要人类明确接受 6.3 写的风险后再打开 |
| D4 | WorkBuddy 通道 | 用 CodeBuddy Code 命令行实现；核验不了订阅登录时先只支持 API 密钥 |
| D5 | 实例能否看到账号下别的会话 | 默认只看自己的；“账号范围列表”“接手他人会话”是两个按实例的开关，默认关 |
| D6 | 云端通道（`codex.cloud`、`claude.cloud`） | 第二批做 `codex.cloud`；`claude.cloud` 等官方有列表和结果接口再做，先留桩 |
| D7 | 是否新增例外九（智能体发消息不逐条审批） | 新增，写法见第 13 节 |
| D8 | 会话桥用什么语言 | Python 3.12（和工作台后端同一套检查与测试工具）；三家的命令行都要 Node，所以镜像里同时有 Node 22 |
| D9 | 会话桥的数据卷是否纳入 `make backup` | 纳入，但登录目录单独加密；也可以选择不备份凭据、恢复后重新登录 |

## 15. 任务拆分

| 顺序 | 任务 | 内容 |
|---|---|---|
| 1 | 核验（ADR） | 在本会话自己的 Docker 里锁定并实测：Codex app-server 的方法与登录、`codex cloud`、Claude 的无界面与会话列表、CodeBuddy 的 `--acp` / `--serve` 与登录方式、OpenAI 与 Anthropic 条款原文；结果写 `docs/decisions/` 和 `docs/versions.md` |
| 2 | 会话桥骨架 | MCP 服务、鉴权、会话与回合、队列、幂等、事件存储、一个假的驱动（测试用）、镜像与 Compose |
| 3 | Codex 驱动 | `codex.dev`、`codex.chat`（同时是 `chatgpt.chat` 的推荐实现） |
| 4 | Claude 驱动 | `claude.dev`、`claude.chat`；登录方式按 D3 |
| 5 | WorkBuddy 驱动 | 按核验结果做，核验不了的部分留桩 |
| 6 | 工作台模块（后端） | 登记会话桥、按实例的权限、工具转发、限额、执行记录、审计、迁移与测试 |
| 7 | 工作台模块（页面） | 会话桥登记、各平台登录、环境维护、会话与回合查看 |
| 8 | 云端通道 | `codex.cloud`；`claude.cloud` 桩 |
| 9 | 验收 | 两个 OpenClaw 实例并发实测需求里的例子；重启恢复；账号不一致；限额；写部署指南 |

第 1 项会用到真实账号的登录。按项目规则不能提交真实凭据，所以需要人类在核验时本人完成登录，或提供测试账号。

## 16. 风险

- **预期落差**：合规范围内看不到 chatgpt.com 和 claude.ai 网页里的对话，也看不到别的机器上的本地会话。这是平台的限制，不是实现能补上的。
- **上游变化**：app-server、Codex cloud、CodeBuddy `--serve` 都不是稳定接口；Anthropic 对订阅用法的政策在收紧。
- **订阅用量**：多个智能体共用一个订阅账号，容易撞到限额；限额不够时只能排队或加账号（再部署一座桥）。
- **开发会话的隔离**：在一个容器里跑多个开发会话，隔离靠目录和许可策略，弱于每会话一个容器；需要强隔离的任务应该走运行服务器。
- **WorkBuddy 的不确定性**最大：公开资料最少，核验结果可能是“只能用 CodeBuddy 的 API 密钥”。

## 17. 以后再做

- 开发会话的“待确认”状态接到工作台审批。
- 环境指向运行服务器上的容器。
- 回合完成后主动通知智能体（经现有的唤醒机制），不用轮询。
- 更多平台（Gemini、Qwen 等有 ACP 入口的智能体）：只需要加驱动。

## 18. 实施记录（与草案不同或草案没写到的地方）

1. **ChatGPT 聊天和 Codex 共用一个账号槽位**（`openai`）：它们用的是同一个 ChatGPT 登录、同一个 `CODEX_HOME`。
   所以一座桥有三个账号槽位：ChatGPT / Codex、Claude、WorkBuddy。
2. **凭据的保存**：草案第 1 节写“不读取、不转发各家的登录令牌”。实施时分两种情况：
   - Codex 的设备码登录：凭据由官方程序自己写进登录目录，桥不接触；
   - API 密钥（三家都支持）和 Claude 的 `setup-token` 令牌：只能通过环境变量交给官方程序，所以管理员在页面上填的值
     保存在数据卷的 `secrets/` 下（文件权限 0600），只在启动官方程序时注入，不出现在接口返回、事件和日志里。
     Claude 用订阅令牌或容器内已有的登录之前，管理员必须先在设置里确认了解第 6.3 节的风险。
3. **回合的结果字段**：失败的回合用 `failure`（`kind`、`message`）说明原因；`error` 只用于工具调用本身出错。
4. **Claude、WorkBuddy 的开发模式分三档**：只读（只给 Read / Glob / Grep）、可写工作区（加 Edit / Write，自动接受修改）、
   可执行命令（`bypassPermissions`）。第三档没有操作系统级的沙箱，命令以容器里的普通用户身份运行，能读到数据卷里的其他内容，
   所以默认不选，要管理员在环境上明确打开。Codex 的“可写工作区”由它自己的沙箱限制在工作目录里。
5. **接口桩**：`codex.cloud`、`claude.cloud` 两个通道存在但返回 `NOT_SUPPORTED` 并说明原因（核验记录第 2、3 节）。
6. **不在别处开的会话**：`llm_session_list(scope=account)` 会把官方程序里有、桥里没登记的会话登记为 `external`；
   有“接手”权限的调用方可以读取和续发它们。

7. **工作台一侧的工具名是 `llm_bridge_*`**（模块的工具名必须以模块名开头），会话桥一侧是 `llm_*`；一一对应，工作台的多一个可选参数 `bridge`。
8. **模块系统加了两个扩展点**（`docs/modules/README.md` 第 3 节）：按实例决定工具是否可见（`tools_for_instance`）、
   模块自己的直接执行工具（`direct_tools`）。按实例的权限存在模块自己的表里，没有改 `agent_instances`。
9. **同机部署**时会话桥加入工作台的 `integrations` 内部网络，工作台用 `http://llm-bridge:8787` 访问（`docs/deployment.md` 第 25 节）。
10. **备份**（D9）：`make backup` 没有纳入会话桥的数据卷。里面主要是登录凭据，重建后重新登录即可；需要保留会话记录的自行备份这个卷。
11. **例外九**：人类 2026-11-13 在对话中明确批准（原话“批准例外九”），已写进 `docs/AGENTS.md` 第 8 节。
    批准之前代理不能自己写入（本会话第一次尝试写入时被权限策略拦下）。

12. **“同一个账号”怎么判断**（第 8 节的细化，T-0229）：平台报告了账号（ChatGPT 设备码登录有邮箱）就按账号比对；
    用 API 密钥或令牌登录时会话桥看不到是哪个账号，统一记成“未知账号”——换一把密钥不算换账号，
    从未知账号换成具体账号（或反过来）才算，要先点“更换账号”。最初的实现拿密钥的指纹当账号，换密钥就要归档全部会话，已改掉。
13. **令牌由部署命令生成**（T-0229）：`make llm-bridge-up` 第一次运行时生成管理令牌写进 `.env`，`make llm-bridge-token` 查看；
    同机部署和设置页的浏览器验收在 CI 的 `ci-llm-bridge` / `workspace` 里跑（`tests/e2e/llm_bridge_browser.cjs`）。

## 参考资料

已打开核验：

- Claude Code 文档：[无界面模式](https://code.claude.com/docs/en/headless)、[会话](https://code.claude.com/docs/en/agent-sdk/sessions)、[云端会话](https://code.claude.com/docs/en/claude-code-on-the-web)、
  [云端环境](https://code.claude.com/docs/en/cloud-environments)、[登录](https://code.claude.com/docs/en/authentication)、[命令行参考](https://code.claude.com/docs/en/cli-reference)、
  [法律与合规](https://code.claude.com/docs/en/legal-and-compliance)
- openai/codex 仓库 main 分支源码与 README（app-server、`codex cloud`、SDK）
- OpenClaw 仓库文档：[MCP 传输](https://github.com/openclaw/openclaw/blob/main/docs/cli/mcp/transports.md)、[MCP 工具](https://github.com/openclaw/openclaw/blob/main/docs/tools/mcp.md)
- npm 包 `@tencent-ai/agent-sdk`、`@tencent-ai/codebuddy-code` 自带的文档
- 第 2 节表格里各项目的 GitHub 页面

只有搜索摘要、未打开原文：`developers.openai.com`（Codex 登录细节）、OpenAI 使用条款、`workbuddy.ai`、`docs.openclaw.ai`（ACP 委派）、关于 Anthropic 无界面额度的报道。
