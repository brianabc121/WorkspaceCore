# V2.0 设计资料包评审（claude，2026-10-31）

> **后续**：评审之后，人类决定用 OpenClaw 接替 AI 方案，并说明 OpenClaw 将运行在另一台服务器上。
> 优化方案见 [`OPENCLAW_PLAN.md`](OPENCLAW_PLAN.md)。本评审第 3 节曾建议“先只做 Codex、OpenClaw 推迟”，现在以人类的决定为准。
> 第 4 节的安全意见（隔离、手机访问、Prompt Injection、审批疲劳、版本锁定）都已落进新方案。

> 评审对象：`docs/v2/package/` 的 8 个文件，重点是 `AGENT_ARCHITECTURE_V2.md` 和 `AGENT_TASKS_V2.md`。
> 评审方法：
> - 通读全部文件，逐一和仓库现状（V1 代码、`docs/`、`.collab/`）对比。
> - 克隆两个上游项目，核对方案引用的接口名：
>   - Codex：`openai/codex` 的 `rust-v0.166.0`（`codex-rs/cli`、`app-server`、`app-server-protocol`）；
>   - OpenClaw：`v2026.10.9`（`docs/gateway/`、`docs/tools/`）。
> - 第 15 节的参考链接（learn.chatgpt.com、docs.openclaw.ai）被本会话的网络策略拦截，没能打开。
>   下文的核验结论都来自上游源码和随仓库发布的文档，不来自这些链接。

## 0. 结论

**方向是对的，安全意识也比多数同类方案强**：明确做“控制面”而不是执行器；不猜接口；审批绑定 payload hash；取消要等远端确认。

**但它现在还不是能直接开工的开发包。** 主要有三类问题：

1. **资料包与仓库现状不一致（P0）**：
   - 规范文件没有按方案自己第 14 节的要求修订；
   - 几个旧文件是过时副本；
   - 有两条规则与现行协作决策冲突。
2. **范围过大**：13 张表、16 个接口、3 个 Provider、Bridge、Reconciler、PWA、成本统计，工作量接近再做一遍 V1。
   业内的通常做法是先做一条最薄的端到端链路（walking skeleton），验证最难的假设，再逐步加宽。
3. **最大的风险只写了原则，没有落到部署形态**：
   - Agent 在哪里、用什么隔离来执行；
   - 手机从外网怎么安全地访问；
   - 怎么防 Prompt Injection。

建议的顺序：

1. 修订资料包（第 2 节）；
2. 人类决定第 7 节列出的事项；
3. 把 V2-S0 收窄为只核验 Codex；
4. 做 MVP 切片；
5. 做安全验收；
6. 再接 OpenClaw 和其余功能。

## 1. 做得好、应当保留的设计

- **边界清楚**：
  - Vikunja 仍是任务主源；
  - `agent_jobs` 只是运行控制记录；
  - session、job、外部 run 三者分开，不把 `thread_id` 当 `task_id`（第 1、8 节）。
- **对“不确定”的处理专业**：
  - `cancel_requested` 只是标志，不是“已取消”的证明；
  - 失联用 `orphaned` / `needs_review`，不自动重试有副作用的操作；
  - “HTTP 200 只代表决策已受理”（第 5、9、10 节）。
  这和 V1 处理“未知完成状态”的做法一致。
- **审批模型**：
  - 审批绑定 job、动作、目标和规范化 payload hash，payload 变了原批准就失效；
  - 超时默认拒绝，AI 不能审批自己的请求；
  - 外层审批不能代替执行主机自己的强制限制（第 9 节）。
- **先核验再写适配器**：V2-S0 先做能力矩阵，查不到的接口显式写 `unsupported` / `unverified`，和 V1 第 8 节第 3 条一致。
- **10 个验收场景具体、可测**（第 13 节），尤其是重复点击、Worker 重启不重复提交、取消必须等远端确认这几条。

**接口核验结果**：方案点名的接口在锁定版本里都找到了，命名没有凭空编造。

| 方案中的名称 | 核验结果 |
|---|---|
| Codex `initialize` / `initialized`、`thread/list`、`thread/read`、`thread/start`、`thread/resume`、`thread/turns/list`、`thread/items/list`、`turn/start`、`turn/interrupt` | 存在（`app-server-protocol`） |
| Codex 服务端发起的审批：`item/commandExecution/requestApproval`、`item/fileChange/requestApproval` | 存在 |
| `codex app-server generate-json-schema` | 存在，但标注为 **experimental**；`app-server` 子命令本身也是 experimental |
| “不要使用已删除的 `codex mcp-server`” | 正确：0.166 没有这个子命令（只有管理 MCP 的 `mcp`） |
| Codex app-server 传输方式 | `--listen` 支持 `stdio://`（默认）、`unix://`、`ws://IP:PORT`；WebSocket 另有鉴权参数 |
| ChatGPT Codex Cloud | CLI 没有 `cloud` 子命令，没找到公开的任务读写接口，方案标为“待验证”是对的 |
| OpenClaw `chat.send`、`sessions.*`、`exec.approval.requested` / `exec.approval.resolve` | 存在（另有 `exec.approval.list/get/waitDecision`） |
| OpenClaw 协议版本 | 当前是 v4。**operator 客户端必须支持当前版本**，N-1 兼容窗口只给 node 角色（`docs/gateway/protocol/versioning.md`） |

## 2. P0：资料包本身的问题（开工前要修）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | `package/DEVELOPMENT_SPEC.md` 与现行规范**逐字节相同**；架构增补第 14 节列出的 §1–§18 修订一项都没做 | “两份文件共同生效，冲突时以 V2 为准”只能靠读者自己合并，最容易漏掉的正好是安全和验收章节 | 由人类把第 14 节落进 `docs/DEVELOPMENT_SPEC.md`；或者只加一节指向 V2 规格，把 V2 明确为独立分册 |
| 2 | `package/TASKS.md`、`package/CODEX_START.md` 是 V1 **最初**的版本 | 现行 `docs/TASKS.md` 已勾选 35/38 项；直接覆盖会丢掉进度和 D-0003 分工 | 本次只存档，没有覆盖。V2 任务应在现行 `docs/TASKS.md` 上追加 |
| 3 | `package/AGENTS.md` 只有项目规则和 V2 补充规则，**没有协作协议**（球权、交接、D-0004 合并、第 9 节） | 直接替换会让两个代理失去协作规则 | 把“V2 补充强制规则”6 条追加到 `docs/AGENTS.md` 第 8 节（人类负责的文件） |
| 4 | 启动指令写给 Codex，但 D-0007 起 Codex 已暂停，所有任务由 claude 完成 | 指令对象不对；按 D-0003 拆分前后端也不再适用 | 改成面向当前执行者，或等人类恢复 Codex 后再按新分工发 |
| 5 | `CODEX_V2_START` 写“不得……自动推送/合并代码”，现行 D-0004 允许 claude 在 CI 全绿时合并本仓库的 PR | 两条规则字面冲突 | 澄清这条约束的是**被控制的 Agent**（Codex 不得自动合并用户的业务仓库），还是开发本项目的代理。按上下文应该是前者，建议写明 |
| 6 | 架构图写“FastAPI BFF + OIDC/RBAC”；V1 是本地账号 + HttpOnly Session，OIDC 是规范里 V1.1 的规划 | V2 是否引入 OIDC 没有交代；手机审批的身份强度取决于这个决定（见 4.2） | 写清 V2 的认证方案；至少给 Critical 审批加二次认证 |
| 7 | 旧 Sprint 6 的 S6-01 ~ S6-03（应用内 AI 助手：对话、只读工具与检索、写工具审批、反 Prompt Injection、A14）被“替换”为 V2-S0 ~ S7，但 V2 里**没有**对话助手和检索功能 | 不清楚是放弃应用内助手还是以后再做；反 Prompt Injection 的测试要求也跟着丢了 | 人类决定：S6-01 ~ 03 是取消、并入 V2，还是保留。T-0047 按决定关闭或保留。反注入测试补进 V2-S6 |
| 8 | “必须先完成旧版 Sprint 0–5”；V2-S7 又列了备份恢复演练、运维手册、回滚 | 这些 V1 都已完成（Sprint 0–5，以及 Sprint 6 的 S6-04 ~ 06） | V2-S7 改为“把 Agent 数据纳入现有备份、运维页和发布 checklist”，不要重做 |
| 9 | 第 15 节的参考链接在本环境无法打开 | 无法确认链接是否就是锁定版本的官方文档 | V2-S0 以锁定 tag 的源码、`generate-json-schema` 输出和实际录制的协议样本为准，链接只作参考 |

## 3. 范围：业内通常怎么控制

**问题**：

- 方案要一次做完：
  - 3 个 Provider；
  - 13 张表、16 个接口；
  - 统一会话中心、PWA；
  - Cron/Hook 映射、成本统计。
- “控制其他 Agent”的难点在**分布式状态**，也就是断线、重启、重复投递、远端状态未知，而不在增删改查。
- 三个 Provider 的语义差别很大：
  - Codex 有 thread/turn；
  - OpenClaw 有 session 和设备配对；
  - Codex Cloud 没有公开接口。
  过早统一抽象，往往得到一个哪家都不好用的最小公分母。

**常见做法：先做一条最薄的端到端链路，用它验证最难的假设。**

建议的 MVP 切片：只支持 Codex app-server 一个 Provider、一个仓库、一种 `coding` 类型的 job。完整链路是：

1. 从一个 Vikunja 任务派发 job；
2. job 在隔离的 runner 里执行；
3. 网页和手机能看到实时事件；
4. 手机上批准或拒绝；
5. 生成 PR，由人在 GitHub 上合并；
6. Worker 重启或断网后，能对账并恢复。

- 这条链路覆盖第 13 节的场景 1、2、3、4、8、9。
- 表只需要 5–6 张：
  - `agent_instances`、`agent_jobs`（带租约、尝试次数等字段，见 4.8）、`agent_events`、`agent_approvals`；
  - `agent_artifacts` 可以先放在 job 的 metadata 里；
  - 任务关联可以先用 `agent_jobs.task_resource_id`。
- 推迟或移出：
  - **OpenClaw 推迟到 V2.1**。它更新太快（见 4.5），而且自带 Control UI、移动节点和审批，最好等 Codex 链路验证了控制面的价值再接。
  - **Codex Cloud 移出 V2 范围**，只在能力矩阵里留一行 `unsupported`。没有公开接口就不该排期。
  - `agent_usage_daily`、`agent_capabilities` 独立表、Cron/Hook 映射、统一会话中心，都可以以后再做。

**先自建还是先集成**：

- OpenClaw 和 Codex 都有自己的界面（OpenClaw Control UI 和移动端，Codex 的 Remote Control 也在试验中）。
- 工作台的独特价值不在“再做一个聊天窗口”，而在于：
  - 和 Vikunja 任务、GitHub PR 关联；
  - 按 workspace 统一权限与审计；
  - 统一的审批入口。
- MVP 应该集中在这些地方。会话详情页先做时间线和审批，不必追平各家的聊天体验。

## 4. 架构与安全

### 4.1 执行隔离（最重要的缺口）

**方案的设计**：“隔离 git worktree + workspace-write sandbox”，“大规模并发应配合独立 runner/VM”。

**问题**：

- worktree 只是隔离了文件目录，**不是安全边界**。
- Codex 的沙箱是一层保护，但 runner 所在的主机或容器才是真正的“爆炸半径”。
- V1 的 Compose 把 API、Worker、PostgreSQL、Redis 和 `.env`（包括凭证加密密钥）放在同一台机器、同一个 Docker 网络。
  如果 Codex runner 也放进这个网络，一次成功的 Prompt Injection 就可能碰到数据库端口和密钥。

**业内做法**：

- **每个 job 一个一次性执行环境**：
  - 容器即可，gVisor、Kata 或 Firecracker microVM 更好，也可以用独立的 VM；
  - 跑完就销毁；
  - 不挂 Docker socket，不和工作台在同一个网络；
  - 设 CPU、内存、磁盘和时长上限。
- **出网白名单**：用 egress 代理只放行代码托管、包管理器和模型 API。这是防止数据外泄最有效的一层。
- **runner 里不放长期凭证**：
  - GitHub 用 GitHub App 的安装令牌：1 小时过期，只授权指定仓库和必要权限（contents、pull requests），由控制面按 job 签发；
  - 模型凭证只给 runner，不进工作台数据库的明文字段。
- **合并由 GitHub 强制拦住，不靠 Agent 自觉**：
  - 分支保护、必需的检查、CODEOWNERS；
  - 令牌没有直接写 `main` 的权限。
  这样场景 9“不自动 push/merge”就有了硬保障。
- **全局开关**：一键暂停所有派发，并中断运行中的 job。

需要人类决定：runner 放在哪里（同机但独立的 Docker daemon，另一台 VM，还是云 runner），以及资源和费用上限。

### 4.2 手机访问路径

**问题**：

- V1 默认只绑定 `127.0.0.1`（`compose.yaml` 的 `HTTP_BIND`）。
- V2 要求手机随时能审批，意味着工作台本身要能从外面访问。
- 方案只写了“手机不直接访问执行器的 WS 端口”，没写工作台怎么暴露。

**业内做法**：

- **不直接暴露到公网**。常用三种方式：
  - Tailscale 或 WireGuard 私网。OpenClaw 自己的文档也推荐 Gateway 只监听 loopback，再用 Tailscale Serve 或 SSH 隧道访问。
  - 零信任代理（例如 Cloudflare Access）加 MFA。
  - 实在要上公网，就必须有 HTTPS、HSTS 和 MFA。
- **Critical 审批要二次认证**：
  - 批准前用 WebAuthn / Passkey（手机上的面容或指纹）重新验证，而不只是靠一个长期有效的 Session Cookie；
  - 手机丢了或会话被劫持时，这是最后一道防线。
- **PWA 推送有前提**：
  - Web Push 需要 HTTPS 和 VAPID 密钥；
  - iOS 只对“添加到主屏幕”的网页应用推送。
  - 推送内容不要带命令原文，只带“有新的审批”，点开后再看详情。

### 4.3 Prompt Injection（方案几乎没覆盖）

**问题**：

- Agent 读到的内容都可能带“指令”：Vikunja 任务描述、GitHub issue 和评论、仓库里的 README / AGENTS.md、依赖包、网页。
- 能写 Vikunja 任务的人，等于能“指挥” Agent。
- 原 S6-03 的反注入测试要求在替换中丢了。

**业内做法**：

- **按输入来源区分信任级别**：
  - 只有 owner/admin 能派发 Write 级 job；
  - 由 webhook 或自动化触发的 job 默认只读。
- **防线放在工具和沙箱层，而不是 prompt 里**：
  - 能不能出网、能不能写哪个目录、能不能 push，由执行环境强制决定；
  - 不能只靠“请不要执行危险命令”这样的提示。
- **审批卡片标出命令来源**：显示这条命令出自哪个 job、哪一轮；批准时要能看到完整参数。
- OpenClaw 仓库自带 `docs/gateway/security/prompt-injection.md`，可以作为 V2-S6 测试用例的参考。

### 4.4 审批疲劳

**问题**：

- 如果每条命令都要审批，用户很快会养成看都不看就点“批准”的习惯。
- 那样审批就形同虚设。

**常见做法**：

- **策略分级**：
  - 只读命令和测试命令按 allowlist 自动放行；
  - 可以按 job 做会话级授权，例如“本 job 内允许 `npm test`”；
  - 只有 Critical 动作推送到手机。
- **统计审批数据**：记下审批次数、平均响应时间和拒绝率，用来调整策略。

### 4.5 上游版本变化快

**现状**：

- Codex 的 `app-server` 和 `generate-json-schema` 都标注为 experimental，不少方法还要打开 `experimentalApi` 才能用。
- OpenClaw 几乎每天发版：`v2026.10.7`、`.8`、`.9` 是连续三天。
- OpenClaw 的 operator 客户端没有 N-1 兼容窗口，Gateway 一升级协议版本，适配器就会连不上。

**常见做法**：

- **锁定版本**：用 tag 加 digest 锁定（和 V1 的镜像规则一致），升级走专门的 PR。
- **用 schema 做兼容测试**：
  - 用生成的 schema 做 diff 测试；
  - 用 V2-S0 录制的协议样本做回放测试；
  - CI 里 schema 一变就失败，提醒人工核对。
- **启动时做版本协商**：适配器启动时协商协议版本，不匹配就显式标为 `unavailable`，其他模块照常运行。这就是场景 5 的思路，应该扩展到“版本不兼容”。

### 4.6 进程与运行时

**一、可能引入第三种常驻运行时**

- 方案说 Codex Bridge 可以用 Node.js/TS 写，也可以用 Python。用 Node 就会多一种要运维的常驻运行时。
- app-server 支持 stdio、unix socket 和 WebSocket，Python 直接说 JSON-RPC 就够了。
- 可以用 `generate-json-schema` 生成 Pydantic 模型，再在 CI 里比对 schema。
- 建议默认用 Python，除非 V2-S0 证明不可行。

**二、一个方案没回答的关键问题：app-server 进程的生命周期**

- 用 stdio，app-server 就是 Worker 的子进程。Worker 一重启，正在跑的 turn 就被杀掉，这和场景 4 冲突。
- 有两种处理方式：
  - **常驻**：app-server 在 runner 里独立常驻，走 unix socket 或带鉴权的 WebSocket，Worker 断开后重连。
  - **接受中断**：接受“重启即中断”，标为 `orphaned`，再用 `thread/resume` 继续会话。
- 这一点应该是 V2-S0 必须回答的问题。

**三、V1 已有、应该直接复用的能力**

方案没有提到这些，应该复用而不是重做：

- 幂等请求表（`idempotency_requests`）；
- Webhook 收件箱和周期对账；
- ARQ Worker 与心跳；
- SSE 推送（带连接数限制和断线重连）；
- 通知中心、审计日志；
- 角色策略 `require_role`（viewer / member / admin / owner）；
- 防 SSRF 的 HTTP 客户端与熔断；
- 凭证加密存储。

`endpoint_ref` 指向的“Secret Store”应该就是现有的加密凭证表，不要再造一个。
注意：现在的 `CREDENTIAL_ENCRYPTION_KEY` 不支持轮换（见 `docs/release-checklist.md`）。加入 Agent 凭证后，轮换能力更该补上。

**四、V1 和 V2 的 SSE 语义不同**

- V1 的 SSE 只发“数据失效”提示，明确不是可靠队列。
- V2 的 job 事件流要求按 `seq` 补发，这需要从 `agent_events` 回放，然后再接实时流。
- 要在同一个 SSE 端点上扩展，还是新开一个端点，应该写进契约。

### 4.7 事件存储

**问题**：

- 如果每个 token 级的 `message.delta` 都入库，一个长任务就会写几万行。
- PostgreSQL 写放大，SSE 推送量也跟着放大。

**常见做法：分两层**：

- **实时层**（Redis Stream 或 pub/sub，保留时间短）传 delta；
- **持久层**只存完成态事件：`message.completed`、`tool.completed`、`approval.*`、`job.*`，delta 合并后再存。

**配套规则**：

- 单条事件的 payload 设上限；
- 设保留期，例如 30 天；
- 数据量大了就按月分区或定期归档。

**对象存储**：方案说“大日志存对象存储并记哈希”，但现有栈里没有对象存储。单用户规模下，用本地数据卷再纳入现有 restic 备份就够了；需要人类决定是否引入 MinIO。

**`seq` 的分配**：

- 不要用 `max(seq)+1`，并发时会冲突。
- 可以在 `agent_jobs` 上放一个计数列，用 `UPDATE … SET last_seq = last_seq + 1 RETURNING` 在行锁内分配；
- 或者保证每个 job 只有一个写入者。

### 4.8 数据模型草稿的具体问题

- **`agent_jobs` 缺字段**：第 5 节自己要求的字段在 SQL 里没有：
  - `dispatch_token`、`lease_owner` / `lease_expires_at`、`attempts`、`last_observed_seq`；
  - 还缺 `updated_at`、乐观锁 `version`、`job_type`、`execution_policy`、`task_resource_id`。
- **状态没有约束**：`agent_jobs.status` 和 `agent_approvals.status` 都没有 CHECK 约束，`agent_instances.provider` 却有。
  状态机字段一旦写进非法值，对账逻辑就会出错，建议都加 CHECK（V1 的用户状态和角色就是这样约束的）。
- **重复点击没有数据库层防护**：
  - 接口要求审批带 `If-Match`，但 `agent_approvals` 没有 `version` 列；
  - `agent_approval_decisions` 没有 `UNIQUE(approval_id)`。
  - 场景 3“重复点击只生成一个决策”就只能靠应用层保证。应该在数据库层兜底。
- **外键没有 `ON DELETE` 规则**：删除 workspace、实例或 job 时的行为没定义。
- **跨 workspace 一致性**：方案提到了，但 SQL 没做。建议用复合外键 `(workspace_id, id)`，保证 job、事件和审批属于同一个 workspace。
- **schema 前缀与 V1 一致**：`workspace.` 前缀和 V1 的 schema 相同，这点是对的。按规则要写成 Alembic 迁移（从 0011 开始），不能直接执行这段 SQL。
- **小问题**：
  - `agent_sessions` 没有 `created_at`；
  - `agent_capabilities` 与 `agent_instances.capabilities` JSONB 重复；
  - `agent_worker_heartbeats` 可以复用现有的 Worker 心跳。

## 5. 测试、运维与成本

- **假 Provider 用真实录制的数据**：
  - V2-S0 从真实实例录下 JSON-RPC / WebSocket 会话，作为契约测试的回放夹具；
  - CI 不依赖真实模型（避免费用、密钥和结果不确定）；
  - 另设一个手动或每晚运行的真实 Provider 冒烟，密钥放在环境的 Secret 里。
- **故障注入**：方案已有，很好。可以用 toxiproxy 一类的工具模拟断网和延迟，和 V1 的 `ci-infra` 验收放在一起跑。
- **可观测性**：
  - 在现有 Prometheus 指标上增加 job 级指标：排队时长、运行时长、审批等待时长、`orphaned` 数量、Provider 可用性；
  - 每个 job 一个关联 ID，贯穿 API、Worker、runner 日志。
- **成本要“限制”，不只“记录”**：
  - 方案只记录 token 和成本；
  - 业内通常还会设单 job 的 token / 时长上限、每日预算，超限就自动中止并通知。
- **审计**：
  - 派发、审批、取消、合并都写进现有审计日志；
  - 最好让审计表只能追加，在数据库层禁止 UPDATE / DELETE。
- **备份**：Agent 配置、job 和审批记录进现有 PostgreSQL 备份；runner 是一次性的，不需要备份。

## 6. 建议的推进顺序

1. **修订资料包**：处理第 2 节的 P0 问题，把规范修订落进 `docs/`。
2. **人类决定第 7 节的事项**。
3. **V2-S0，只核验 Codex**：
   - 锁定 0.166.x；
   - 生成并提交 schema；
   - 录制会话、审批、中断、恢复的样本；
   - 回答“stdio 还是常驻”和 `thread/resume` 能恢复到什么程度；
   - 写 ADR。
4. **MVP 切片**（第 3 节）：5–6 张表，复用 V1 的基础设施，runner 隔离按 4.1 的方案。
5. **安全验收**：场景 2、3、7、9，加上 Prompt Injection、越权和审批绕过测试。
6. **OpenClaw 适配器**（V2.1）。
7. **其余功能**：统一会话中心、Cron/Hook、成本统计、更多 Provider。

## 7. 需要人类决定或提供的事项

| # | 事项 | 为什么需要 |
|---|---|---|
| 1 | 是否采纳 V2，替代 Sprint 6 的 AI 部分（S6-01 ~ 03）；应用内 AI 助手是取消还是以后再做 | 决定 T-0047 和 `docs/TASKS.md` 怎么改 |
| 2 | MVP 范围：先只做 Codex 单 Provider？OpenClaw 推迟到 V2.1？Codex Cloud 移出范围？ | 决定第一阶段的工作量 |
| 3 | runner 放在哪里（独立 VM / 同机独立 Docker daemon / 云 runner），以及资源和费用上限 | 隔离方案和部署方式取决于它 |
| 4 | Codex 怎么认证（ChatGPT 账号登录还是 API Key），费用由谁承担 | 真实核验和运行都需要；凭证放进环境 Secret，不要在对话里提供 |
| 5 | 手机怎么访问（Tailscale / 零信任代理 / 公网 + HTTPS + MFA），Critical 审批是否要求 Passkey | 决定暴露方式和认证改造的范围 |
| 6 | GitHub 写权限用 GitHub App（推荐）还是细粒度 PAT；允许操作哪些仓库 | PR 创建和最小权限需要 |
| 7 | 是否已有 OpenClaw 实例、用哪个版本；不急的话就推迟 | V2-S3 的前提 |
| 8 | 规范文件（`docs/DEVELOPMENT_SPEC.md`、`docs/AGENTS.md`、`docs/TASKS.md`）由人类修订，还是授权 claude 起草 PR 再由人类批准 | 这些是人类负责的文件，按 D-0004 第 3 节要人类决定 |
| 9 | 会话网络：V2-S0 需要访问上游文档站点（目前被网络策略拦截）、npm/PyPI 和 GitHub Releases | 在环境设置里放开对应域名，或改用更宽的访问级别 |

**不需要等这些决定、claude 现在就能做的**：

- 起草规范修订 PR，供人类审批；
- 把第 4.8 节的数据模型改写成 Alembic 迁移草案；
- 用上游源码生成 Codex schema，做离线的契约分析（不需要模型凭证）。

**必须等人类的**：

- 真实 Provider 的连接与录制（需要凭证和 runner）；
- 手机外网访问（需要选定方式和域名）；
- GitHub App 的创建。
