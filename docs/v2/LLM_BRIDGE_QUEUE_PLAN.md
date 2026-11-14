# MCP 管理器优化：按平台限流、会话内容落库、消息队列、插入消息（优化方案）

> 人类 2026-11-14 的五条要求（T-0236）：
>
> 1. 限流：一个 MCP 管理器控制的一个大模型平台账号，最多同时有 3 个真正进行中的会话（3 个 Claude / 3 个 Codex / 3 个 WorkBuddy…），避免账号频繁请求被封。
> 2. 每个会话的内容（文字、图片、文件）保存在 MCP 管理器容器的数据库里，工作台直接看历史，不用再向大模型请求。
> 3. 消息队列：会话在跑任务时再发的问题不打断它，进队列，前面的跑完再发。
> 4. 插入消息：默认进队列；也可以选择把消息直接送给正在跑任务的会话，让大模型一起处理。
> 5. 一键部署 MCP 管理器的使用说明（`docs/guides/mcp-manager-deploy.md`）。
>
> 人类同日定的三点：**按平台计数**；某个平台做不到插入时**降级成“中断后合并重发”**；官方程序里已有的旧会话**第一次打开时导入落库**。
>
> 本文是方案，代码按它落实。MCP 管理器本身见 `LLM_BRIDGE_PLAN.md`，一键部署见 `LLM_BRIDGE_DEPLOY_PLAN.md`。

## 1. 现状（2026-11-14 在 main `8791506` 上核对）

| 需求 | 现在 | 差距 |
|---|---|---|
| 限流 | `concurrency` 默认 2，按**账号槽位**计数（ChatGPT 与 Codex 共用 openai 槽位，合计 2），超过就排队；`send_per_hour` 按调用方 | 默认 3、按平台、起跑间隔 |
| 落库 | 管理器建的会话：提问、最终回答、挑选过的过程事件在库里；只传文字；旧会话（`origin=external`）每次向官方程序读 | 图片与文件、完整过程、旧会话导入 |
| 队列 | 已有：同一会话的新消息排到队尾（`turns.status=queued`），重启后继续，可取消 | 看不到队列；不能改内容、调顺序 |
| 插入 | 没有。Claude / WorkBuddy 一回合一个进程，写完问题就关输入；Codex 长驻但只用了 `turn/start`、`turn/interrupt` | 驱动要支持回合中追加 |

第三方能力（锁定版本，记录在 `docs/decisions/2026-11-14-llm-bridge-steer-verification.md`）：

| 程序 | 回合中追加 | 图片输入 |
|---|---|---|
| Codex 0.169.0 | `turn/steer{threadId, expectedTurnId, input[]}`（生成的 schema） | `input` 项类型 `text` / `image` / `localImage{path}` |
| Claude Code 2.1.323 | `-p --input-format stream-json`（“realtime streaming input”）、`--replay-user-messages`（帮助文本）；运行时行为没有账号、不能实测 | stream-json 的 user message 里带 image 内容块 |
| CodeBuddy Code 2.184.0 | 同上两个参数（帮助文本）；运行时行为不能实测 | 同上 |

## 2. 限流

- 计数维度改成**平台**：`chatgpt`、`codex`、`claude`、`workbuddy` 各自一个名额池。设置项 `concurrency` 的含义改成“每个平台同时进行的会话数”，**默认 3**（范围 1–16）。已经保存过这个设置的管理器保留原值，页面上可以改。
- 一个会话同一时间只跑一个回合，所以“进行中的回合数”就是“进行中的会话数”；页面和文档都写“会话”。
- 超过上限：排队等名额，不拒绝。状态里给出每个平台 `running`、`waiting`。
- 新增 `min_start_interval_seconds`（默认 2，范围 0–60）：同一个平台两次开始之间至少隔这么久，三个名额不会同时起跑。
- `send_per_hour`（按调用方）不变。

## 3. 会话内容落库

新表（管理器的 SQLite，`SCHEMA_VERSION` 2，启动时自动迁移）：

- `messages`：`id`、`session_id`、`turn_id`、`seq`（会话内递增）、`role`（`user` / `assistant` / `process` / `system`）、`kind`（`text` / `command` / `tool` / `tool_result` / `files` / `notice` / `image` / `file`）、`text`、`data`（JSON：工具名、参数、退出码、用量…）、`by`（谁发的）、`delivery`（用户消息：`turn` / `inserted` / `merged`）、`at`。
- `attachments`：`id`、`session_id`、`message_id`、`direction`（`input` / `output`）、`name`、`media_type`、`size`、`sha256`、`path`（数据卷里 `attachments/<会话>/<id>` 的相对路径）、`at`。

规则：

- 每条提问、每段回答、每个过程事件都写 `messages`（原来没记的读文件、搜索、其他工具调用和工具结果也记；工具结果超过 64 KB 截断并注明）。回答与过程照旧经过脱敏。
- **输入附件**：发送时可以带图片和文件（每条最多 8 个、单个 20 MB，类型白名单：常见图片、文本、PDF、压缩包不解压）。管理器把它们存进附件目录并复制到会话的工作目录；图片按各平台支持的方式传给模型（Codex `localImage`，Claude / WorkBuddy image 内容块），其他文件在提问里注明相对路径，由模型自己读（需要这个会话有读文件的权限，纯聊天会话只支持图片）。
- **输出附件**：模型产出的图片（Codex 的 `imageGeneration` 条目）和开发会话里改动、新建的文件（回合结束时按变更列表复制，单个 20 MB、每回合 50 个为限）存成附件。
- **旧会话导入**：`origin=external` 的会话第一次打开时向官方程序读一次全部内容，写进 `messages`，标记 `imported_at`；之后读库。页面上有“重新导入”（官方程序里又有了新内容时用）。
- 读接口 `GET /admin/sessions/{id}` 改成从 `messages` 拼；新增 `GET /admin/sessions/{id}/attachments/{attachment_id}`（原样下载，`Content-Disposition: attachment`，图片可内联）。
- 已有的会话：迁移时把 `turns` / `events` 里已有的内容回填进 `messages`，旧数据不丢。

## 4. 消息队列

机制不变（按会话串行、持久化、重启后继续），补上查看与管理：

- `GET /admin/sessions/{id}/queue`：这个会话排队中的消息（顺序、谁发的、什么时候、内容摘要）和正在跑的那一条。
- `PATCH /admin/turns/{turn_id}`：改排队中消息的内容；`POST /admin/turns/{turn_id}/move`：调到队首或队尾。只对 `queued` 有效，已经开始的返回 `409`。
- 取消排队中的消息沿用 `llm_turn_cancel`。
- 队列按 `position` 排序（新列），不再只看入队时间。

## 5. 插入消息

`llm_send` 和聊天页的发送多一个参数 `mode`：`queue`（默认）/ `insert`。

- 会话空闲时两种一样：直接开始一个回合。
- 会话在跑、`mode=insert`：
  1. 驱动支持回合中追加（`steer`）：把消息直接送进正在跑的回合；记一条 `delivery=inserted` 的用户消息，不产生新回合。Codex 用 `turn/steer`；Claude / WorkBuddy 的回合进程改成 `--input-format stream-json` 启动、回合期间不关输入，追加时写入一条 user message。
  2. 追加失败（平台拒绝、回合刚好结束、驱动不支持）：**降级成“中断后合并重发”**——中断当前回合，立刻用“〈新消息〉+ 请接着刚才没做完的事继续”开始一个新回合，排在这个会话队列的最前面；用户消息记 `delivery=merged`，被中断的回合记 `interrupted`（不是失败）。
- 插入只对**这个会话**有效，受同样的权限检查（智能体要有写权限；接手别人的会话仍要确认），计入每小时发送次数。
- 返回里写明实际用了哪种（`delivered: inserted | merged | started`），页面照实显示。
- Claude / WorkBuddy 的回合中追加没有账号实测：实现按官方参数的说明写，并做成“写入后等官方程序回显确认（`--replay-user-messages`）；规定时间内没有确认就走降级”。真实账号验收时确认实际行为，记进核验记录。

## 6. 工作台

- 接口（`/api/v1/llm_bridge/bridges/{id}/chat/...`）：发送支持 `mode` 和附件（先 `POST .../attachments` 上传得到编号，再随消息带上）；`GET .../sessions/{sid}/queue`、改内容、调顺序；附件下载代理（校验工作空间与角色，不暴露管理令牌）。
- 智能体工具：`llm_bridge_send` 多 `mode`；新增只读的 `llm_bridge_queue`。附件先不对智能体开放（以后再做）。
- 聊天页：输入框旁边“排队 / 插入”的选择（会话在跑时才出现，默认排队）；可以粘贴、拖入、选择图片和文件；消息里显示图片缩略图和文件；会话上方显示“队列里有 N 条”，展开可取消、修改、置顶；过程里显示完整的工具调用。
- 管理页：设置里“每个平台同时进行的会话数”“两次开始的最小间隔”；状态里每个平台的进行中 / 等待数。

## 7. 安全与数据

- 附件只在管理器的数据卷里，工作台不另存；经工作台下载要 owner / admin。文件名净化、类型按内容嗅探、大小上限；不执行、不解压。
- 提问原文现在没有脱敏就落库，保持不变（这是用户自己的输入）；回答、过程、导入的旧会话内容照旧脱敏。
- 插入不绕过任何检查：限额、密钥拦截、账号一致性、接手确认都和普通发送相同。
- 数据卷会变大：设置里加“附件保留天数”（默认不清理），状态里显示附件占用。

## 8. 分步

| 步 | 内容 | 交付 |
|---|---|---|
| 一 | 按平台限流与起跑间隔；队列的查看、修改、置顶；使用说明 | PR 1 |
| 二 | `messages` / `attachments`、完整过程、回填、旧会话导入；输入输出附件；聊天页显示 | PR 2 |
| 三 | 插入消息（三个驱动的回合中追加 + 降级）、聊天页的“排队 / 插入” | PR 3 |

每一步都带测试（管理器的单元测试用仿真命令行，工作台的接口与页面测试，浏览器验收），文档随改。

## 9. 取舍与风险

- **按平台计数**意味着同一个 OpenAI 账号上 ChatGPT 和 Codex 合计最多 6 个进行中的会话（人类 2026-11-14 决定）。担心这个账号时，把上限调小即可。
- Claude / WorkBuddy 的回合中追加是按官方参数实现的，没有账号实测；不符合预期时自动走降级，不会丢消息。
- 输出文件按“变更列表”收集，模型用别的办法写出的文件可能收不到；页面上写明。
- 旧会话导入只导文字（官方程序的会话文件里图片是内嵌数据的会一并存成附件，其余不保证）。

## 10. 实施记录

**第一步（PR 1）**——按平台限流、队列管理、使用说明：

- `concurrency` 默认 3、按平台计数；`min_start_interval_seconds` 默认 2。间隔的默认值是部署级配置
  `BRIDGE_DEFAULT_MIN_START_INTERVAL`（测试和验收环境设成 0），管理员在页面上改过之后以页面上的为准。
- 队列顺序存在 `turns.position`（旧库启动时自动加列并按入队时间回填，`SCHEMA_VERSION` 2）；会话的处理程序每次从库里取队首，
  所以调过顺序、改过内容的都按最新的来。会话记录按实际处理的顺序显示（原来按入队时间）。
- 管理接口 `GET /admin/sessions/{id}/queue`、`PATCH /admin/turns/{id}`、`POST /admin/turns/{id}/move`；工具 `llm_queue`（只读）。
  状态里多了 `load`。工作台的接口、聊天页的队列面板、设置里的两项见 `docs/api-contract.md`。
- 与方案的差别：没有。第 4 节说的“取消沿用 `llm_turn_cancel`”照做。
