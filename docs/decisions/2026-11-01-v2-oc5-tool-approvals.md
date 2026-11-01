# V2 OC-5：MCP 工具调用记录与审批的实现决定

- 状态：accepted
- 日期：2026-11-01
- 作者：claude（协作任务 T-0057，OC-5 第 1 批）
- 依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.6 节；`docs/decisions/2026-10-31-v2-oc1-contract.md` 第 2、6 节
- 范围：第 1 批是记录表、审批流程和执行器；第 2 批是 `POST /mcp`（第 8 节）与读工具（第 9 节）；第 3 批是审批通知（第 10 节）、IM 审批卡片（第 11 节）与 `source` 推断（第 12 节）；后续见第 13 节

契约已经定了接口形态，这里记录实现时做出的、契约里没有写死的决定。

## 1. 记录表 `ai_tool_executions`（迁移 0017）

- 每次工具调用一行：读工具直接是 `succeeded` / `failed`；权限不够是 `denied`；写工具和高危工具先是
  `pending_approval`，之后是 `rejected` / `expired`，或者 `executing` → `succeeded` / `failed` / `unknown`。
  状态和风险等级都有 CHECK 约束。
- 实例被删除时记录保留（`instance_id` 置空，另存 `instance_name`）；工作空间删除时一起删除。
- 同一实例、同一工具、同样参数同时只能有一条待审批：部分唯一索引
  `(instance_id, tool_name, arguments_hash) WHERE status = 'pending_approval'`。并发的同样请求只会建一条，
  后到的拿到已有那一条（`deduplicated: true`）。
- 只新增表，`downgrade` 删表即可回退；没有改动已有数据。

## 2. 执行身份

- 批准后以实例的登记人（`acting_user_id`）的身份执行，角色取“登记人在工作空间里的角色”和实例 `max_role`
  中较低的一个（契约第 2 节）。审批人只决定“做不做”，不会把自己的权限借给 AI。
- 执行前重新检查：实例已停用或已删除、总开关关闭、登记人已不是成员或角色不够，都以 `failed` 结束
  （`AGENT_DISABLED` / `FORBIDDEN_ROLE` / `FORBIDDEN_SCOPE`），不执行。
- 执行器直接调用已有的领域服务（任务、日程、书签、通知、自动化），所以工作空间范围、角色校验、审计、
  Vikunja 的超时与重试、结果未知的处理都沿用 V1 的实现，没有另写一套。
- 幂等键是记录 ID：任务写操作和触发自动化用它作 `Idempotency-Key`，同一条审批不会执行两次。
  批准时先把状态改成 `executing` 并提交，再执行；进程中途退出时记录停在 `executing`，不会被再次批准。

## 3. 参数的保存与脱敏

- 写和高危工具的参数经工具的 Pydantic 模型校验并规范化后保存，执行时只用存下的这份，AI 之后改不了。
- `arguments` 列只在待审批期间保存完整参数；处理完（执行结束、拒绝、过期）就清空为 NULL。
  读工具和 `denied` 记录不保存完整参数。
- 工作流里标为敏感（`sensitive: true`）的触发参数（例如邮箱、单号）：
  - 取值用 `CREDENTIAL_ENCRYPTION_KEY` 加密后再存（AES-256-GCM，绑定到这条记录和参数名），
    数据库与备份里没有明文；执行时解密；
  - 展示版本 `arguments_display` 里是 `***`，接口只返回展示版本；
  - `denied` 的触发请求没有查过哪些参数敏感，展示版本里全部参数值都是 `***`。
- 去重用的 `arguments_hash`：配置了凭证密钥时是 HMAC-SHA256（密钥由凭证密钥派生，用途标签
  `ai_tool_arguments`），敏感参数的取值不能用字典从哈希反推；没配置密钥时是 SHA-256
  （这时也不可能有带敏感参数的工作流，因为 n8n 连接本身就需要凭证密钥）。
  轮换凭证密钥后，进行中的待审批可能与新请求算出不同的哈希，最多多出一条待审批，不影响正确性。

## 4. 审批

- `approve` / `reject` 需要会话、CSRF 与 `If-Match`；`If-Match` 不等于当前 `version` 返回 409
  `VERSION_CONFLICT`；不是待审批或已过期返回 409 `EXECUTION_NOT_PENDING`（顺带把过期的记录改成 `expired`）。
- 只有 owner / admin 能处理（契约第 6 节）；member 及以下在列表里只看到 `acting_user_id` 是自己的记录，
  `pending_count` 是全工作空间的待审批数。
- 高危操作（`delete_task`、`delete_calendar_event`、`trigger_automation`）要带当前用户的密码：
  没带 403 `STEP_UP_REQUIRED`，错误 403 `STEP_UP_FAILED`。密码错误按用户计数，15 分钟内 5 次后返回
  429 `RATE_LIMITED`（带 `Retry-After`），与登录失败的限流共用 `FailureLimiter`。
- 每个实例最多 20 条待审批，超过返回工具错误 `TOO_MANY_PENDING`；待审批 15 分钟没人处理就过期
  （worker 每分钟一次，发起新请求和处理审批时也会顺带检查）。

## 5. 版本保护

- 修改、完成、删除任务和删除日程时，创建审批那一刻记下目标的版本（`target_version`）；执行时把这个版本交给
  领域服务，版本已变就以 `failed` / `VERSION_CONFLICT` 结束，不覆盖别人的修改。
- 新建类操作（新建任务、日程、书签）和标记通知已读没有目标版本。

## 6. 结果未知

- 领域服务返回 `RESULT_UNKNOWN` / `WRITE_UNKNOWN` 或 504 时记 `unknown`，不自动重试，提示到对应页面确认。
  沿用 V1 写操作“未知完成状态”的处理方式。

## 7. `summary`

- 由工作台按参数和查到的目标生成（例如“删除任务「写周报」”），不采用 AI 给的文字；
  标题截断到 60 个字符。

## 8. `POST /mcp`（第 2 批）

- **SDK 用法**：用 MCP Python SDK 2.3.0 的底层 `Server` 加 `StreamableHTTPSessionManager`
  （无状态、JSON 响应），没用 OC-0 测试时的 `MCPServer`。
  `MCPServer` 按函数签名生成参数 schema，而工具的参数模型已经在 `app/schemas/ai_tools.py` 里
  （禁止多余字段，写工具复用 V1 的创建模型），`tools/list` 直接用这些模型的 JSON Schema。
  传输与 OC-0 核验时相同（Streamable HTTP、无状态、JSON 响应），OpenClaw 那一侧不受影响。
- **挂载方式**：`POST /mcp` 是普通的 FastAPI 路由，依赖 `instance_principal` 做认证，与 `/agent-api` 完全相同
  （失败一律 401 `AGENT_UNAUTHORIZED`，外网入口另需客户端证书，外网失败 10 次封禁）。
  认证通过后，把还没读过的请求原样交给 SDK。
  SDK 在自己的任务组里处理请求，contextvar 传不过去，所以实例 ID 放在 ASGI scope 的 state 里，
  工具处理函数从 `ctx.request.state` 读取。
- **只接受 POST**：无状态模式下 GET 会打开一条一直不结束的 SSE 流（本地实测），DELETE 没有会话可删，
  两者都返回 405。MCP 规范允许服务器对 GET 返回 405。
- **SDK 的会话管理器**放在应用自己的后台任务里运行（`McpRunner`）：它内部的 anyio 任务组必须在同一个任务里
  进入和退出，而 lifespan 的启动和关闭不保证在同一个任务里。
- **工具清单**：
  - 只列实例权限范围里的工具（`read` 范围的实例看不到写工具）；
  - 角色在调用时再检查，因为登记人的角色可能变化；
  - 没有实现的工具不列出，调用返回 `NOT_FOUND`；
  - 工具注解标明只读和破坏性（`readOnlyHint`、`destructiveHint`）。
- **限流**：每次 `tools/call` 都计数（Redis 固定窗口，按实例每分钟 120 次），超出返回工具错误 `RATE_LIMITED`，
  不返回 HTTP 429：OpenClaw 把工具错误当作工具结果交给模型，模型能看懂要等一等。
- **错误**：工具错误是 `isError: true`，结构化内容为 `{"error": {"code", "message"}}`。
  领域服务的错误换成契约里的错误码：404 → `NOT_FOUND`，422 → `VALIDATION_FAILED`，429 → `RATE_LIMITED`，
  其余 → `EXTERNAL_UNAVAILABLE`。错误信息里不带内部细节。
  意外异常由 SDK 返回统一的 JSON-RPC 内部错误，不透出异常信息。
- **记录**：
  - 读工具每次调用写一条 `succeeded` / `failed` 记录；
  - 权限不够写 `denied`；
  - 参数校验不通过的调用不写记录：参数不合法，没有可存的规范化参数，AI 也只是拿到错误。

## 9. 读工具（第 2 批后半）

- 每个读工具调用工作台页面用的同一个领域服务，身份同写工具（登记人，角色取两者中较低的一个），
  所以工作空间范围和可见性与页面一致，例如别的成员的私有收藏看不到。
  搜索和收藏列表原来要求登录会话（`AuthContext`），现在只需要用户 ID，页面和 MCP 都按用户 ID 过滤私有收藏。
- 第三方或用户写的文字放进每个条目的 `untrusted`（契约第 9.3 节）。
  测试里放了一条写着诱导指令的任务、日程和收藏，检查这段文字只出现在 `untrusted` 里。
- 读外部数据的工具（任务、项目、仓库、Issue、PR）带 `freshness`，取值同页面上的同步状态；
  `unauthorized` 归为 `unavailable`，因为契约的 `Freshness` 没有这个值。
  没有配置的集成返回空列表加 `not_configured`，不当作正常的空数据。
- 没有分页参数的列表（项目、知识来源、工作流、日程）最多返回 50 条（契约：列表最多 50）。
- `dashboard_overview` 不走首页聚合（那里会建默认仪表盘、要排队），直接算几个数字：
  - 未完成和逾期任务数：没配置 Vikunja 时为 null；
  - 今天的日程数：按 UTC 计算今天，工作台没有工作空间级的时区设置；
  - 未读通知数、待审批数；
  - `down` 的服务数：用运维页同一套部署健康检查，带缓存。

## 10. 审批通知（第 3 批前半）

- 新的待审批（不含去重返回的那一条）写通知 `approval_requested`，发给本工作空间的 owner / admin（能审批的人），与记录在同一个事务里提交。
  - 高危操作的级别是 `warning`，正文提示批准时要重新输入密码；写操作是 `info`。
  - 标题是“「实例名」请求：summary”，summary 由工作台生成（第 7 节）。
- 过期（worker 清理，或处理时发现已过期）写通知 `approval_expired`，级别 `info`。
- 通知的链接是 `/approvals?id=<记录 ID>`，指向前端的审批中心（T-0056 实现这个页面）。
- 顺带修正：已有的 IM 通知（自动接力暂停、好友关系恢复）链接写的是 `/im?conversation=…`，但 IM 页读的查询参数是 `c`，点开不会选中会话。已改为 `/im?c=…`。

## 11. IM 审批卡片（第 3 批后半）

在锁定版本 v3.8.3-patch.18 的源码里核实，并在本会话的 OpenIM 实例上实测：
- 只有通知账号（`AppMangerLevel ≥ 3`，`AppNotificationAdmin`）能发 OA 通知（contentType 1400）。`/msg/send_msg` 会把会话类型改成 4；普通账号发送返回 1002；
- 会话 ID 是 `sn_` 加排序后的发送方和接收方（`pkg/msgprocessor/conversation.go`）；
- `/user/add_notification_account` 遇到已存在的用户 ID 返回 1001。已有的普通账号不能升级，`/user/update_notification_account` 只改昵称和头像；
- 拉取到的 OA 通知 content 是 `{"detail": "<OA 元素的 JSON>"}`，消息的 ex 原样保留；
- OA 通知不经过发送前回调，但会触发“单聊发送后”回调（`sessionType: 4`）；
- 通知账号可以是群成员；它在群里发文字时会走到发送前回调，由回调按用户 ID 放行。

决定：
- **每个工作空间一个通知账号 `wn_<工作空间 UUID 十六进制>`**，第一次发卡片时注册为通知账号。这偏离了契约原来写的 `ws_notice`：通知会话按发送方和接收方区分，共用一个账号时，同一个人在两个工作空间收到的卡片会落进同一个 `sn_` 会话，违反“不得跨工作空间泄露”。`ws_notice` 保留，继续在群里发系统提示。已同步修改 OC-1 契约第 5.1、5.3、6、13 节。
- **发卡片交给 worker**（作业 `send_approval_cards`），MCP 调用不等 OpenIM：
  - 新的待审批提交后投递作业，投递失败只记日志；
  - 作业给本工作空间每个 owner / admin 发一条，内容是“「实例名」请求：summary”、风险提示和 15 分钟时限；
  - 同一条记录、同一个人只发一次（Redis 标记，发送失败时撤销）；记录已处理时不发。
- **卡片只是提醒和入口**：批准、拒绝仍走工作台的审批接口；在 IM 里回复文字不算批准；通知会话不能发消息（409 `IM_SEND_REJECTED`）。
- **投影**：
  - 识别 `sn_` 会话：管理员加本工作空间的通知账号，管理员仍是这个工作空间的 owner / admin 时才建会话行；
  - 只有工作空间通知账号发的 1400 消息、ex 带审批 ID 时才是 `approval_card`。实例或其他账号发的同样内容不算，防止伪造；
  - 消息里只记审批 ID，状态以审批记录为准；
  - 发送后回调里 `sessionType` 为 4 时按 `sn_` 会话处理。
- 顺带：`OpenIMClient` 的 repr 不再包含 secret，异常回溯里不会出现。

## 12. `source` 的推断

- MCP 请求不带会话信息时（契约第 6 节），工作台取这个实例**最近 10 分钟内**收到的一条消息作为来源：
  - 管理员（`ws_` 账号，不含 `ws_notice`）在与它的单聊里发的消息；
  - 或者管理员在它所在的群里 @ 它的消息。
- `source` 记下这条消息的会话 ID、消息 ID 和发送者，`inferred` 为 true；找不到时各字段为空，`inferred` 仍为 true。
- 只在待审批记录上推断，读工具和 `denied` 记录不推断。
- OpenClaw 是否在 MCP 请求里带会话标识，本次没有核实（需要运行真实 OpenClaw）。按推断实现，留给 OC-7 验收（T-0059）用真实 OpenClaw 核对；如果它带了，再改为直接采用。

## 13. 后续批次

- 审批中心、活动页和卡片上的批准 / 拒绝按钮（前端）在 T-0056 里做。
