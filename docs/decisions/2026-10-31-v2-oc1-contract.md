# V2 接口契约（OC-1）：实例、密钥库、IM、工具审批、实例接口、MCP 工具、OpenIM 回调

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0053）
- 依据：
  - `docs/v2/TRANSFORMATION_PLAN.md`（下称“方案”）第 3 ~ 5、7 节；
  - `docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`（下称“OC-0 ADR”）；
  - 规范第 6.1 节（V2 修订）、第 8 节项目规则 6 ~ 9 与 V2 补充规则。
- 实现：
  - OC-2（T-0054）：实例、凭证、密钥库、接入入口、`/agent-api` 的取密钥与心跳；
  - OC-3（T-0055）：IM 后端、`/agent-api/v1/im-token`、接入网关、OpenIM 回调；
  - OC-4（T-0056）：管理员端页面；
  - OC-5（T-0057）：MCP 服务器与审批。
- 代码里的契约：
  - `services/api/app/schemas/agents.py`、`im.py`、`ai_tools.py`、`openim_callbacks.py`；
  - 接口桩：`services/api/app/api/v1/agents.py`、`im.py`、`ai.py`，`services/api/app/api/agent_api.py`；
  - 契约测试：`services/api/tests/test_contract.py` 的“V2 OC-1 契约”部分。

## 1. 原则

- **只增不改**：V1 的请求、响应字段和枚举都不变。唯一改动已有类型的地方是 SSE 的 `topics` 增加三个值（第 11 节），前端的映射表同时补上。
- **三类入口，各自认证**：

| 入口 | 路径 | 谁来调用 | 认证 | 进 OpenAPI |
|---|---|---|---|---|
| 管理员入口（原有） | `/api/v1/agents/*`、`/api/v1/im/*`、`/api/v1/ai/*` | 浏览器 | 会话 Cookie + CSRF + `X-Workspace-Id` | 是 |
| 接入入口（内网、外网各一个站点） | `/agent-api/v1/*`、`POST /mcp`、`/im/ws`、`/im/api/*`、`/im/obj/*` | OpenClaw 实例 | 实例凭证（外网另需客户端证书）；`/im/*` 认 IM 令牌 | 只有 `/agent-api/v1/*`（`InstanceToken`） |
| 内部网络 | `/internal/openim/{path_token}/{command}`、`/internal/gateway/im-auth` | OpenIM、Caddy | 随机路径 + 来源网段；Caddy 内部调用 | 否 |

- **路由隔离靠 Caddy，API 再检查一次**：
  - 管理员入口只把 `/api/*` 转给 API，所以 `/agent-api`、`/mcp`、`/internal` 从浏览器那边到不了；
  - 接入入口默认拒绝，只放行上表的路径，其余返回 404；
  - 接入入口给请求加 `X-MyWS-Ingress: intranet|public`（外网另加 `X-MyWS-Client-Cert-SHA256`）。管理员入口删掉这两个请求头。
  - API 只在请求的直接对端属于 `AGENT_INGRESS_TRUSTED_PROXIES`（接入入口 Caddy 的地址或网段）时才相信它们，不看 `X-Forwarded-For`；同一网络里其他容器带这两个请求头也不算。为空时实例接口一律 401（OC-2 实现）。
- **实例凭证不能调用管理员接口，会话也不能调用实例接口**。审批只在管理员接口里，所以 AI 无法批准。
- 错误格式沿用规范第 6 节；新错误码见第 12 节。未实现的端点返回 501，但不写进 OpenAPI。

## 2. 端点总览

### 2.1 管理员接口（`/api/v1`）

| 方法 | 路径 | 角色 | 成功响应 | 实现 |
|---|---|---|---|---|
| GET / PATCH | `/agents/settings` | owner | `AgentSettingsOut` | OC-2 |
| GET / POST | `/agents/instances` | owner | `AgentInstancePage` / 201 `AgentInstanceOut` | OC-2 |
| GET / PATCH | `/agents/instances/{instance_id}` | owner | `AgentInstanceOut` | OC-2 |
| POST | `/agents/instances/{instance_id}/disable`、`/enable` | owner | `AgentInstanceOut` | OC-2 |
| POST | `/agents/instances/{instance_id}/bundle` | owner | 200 `application/zip` | OC-2 |
| POST | `/agents/instances/{instance_id}/credentials/{credential_id}/revoke` | owner | `AgentInstanceOut` | OC-2 |
| PUT | `/agents/instances/{instance_id}/secret-grants` | owner | `AgentInstanceOut` | OC-2 |
| GET / POST | `/agents/secrets` | owner | `AgentSecretPage` / 201 `AgentSecretOut` | OC-2 |
| GET | `/agents/secrets/accesses` | owner | `SecretAccessPage` | OC-2 |
| PATCH / DELETE | `/agents/secrets/{secret_id}` | owner | `AgentSecretOut` / 204 | OC-2 |
| POST | `/agents/secrets/{secret_id}/rotate` | owner | `AgentSecretOut` | OC-2 |
| GET | `/im/status`、`/im/badges` | owner / admin | `ImStatus`、`ImBadges` | OC-3 |
| GET / POST | `/im/conversations` | owner / admin | `ImConversationPage` / `ImConversationOut` | OC-3 |
| PATCH | `/im/conversations/{conversation_id}` | owner / admin | `ImConversationOut` | OC-3 |
| POST | `/im/conversations/{conversation_id}/read` | owner / admin | `ImConversationOut` | OC-3 |
| GET / POST | `/im/conversations/{conversation_id}/messages` | owner / admin | `ImMessagePage` / 202 `ImMessageOut` | OC-3 |
| GET / POST | `/im/conversations/{conversation_id}/pins` | owner / admin | `ImMessagePinList` / 201 `ImMessagePinOut` | OC-3 |
| DELETE | `/im/conversations/{conversation_id}/pins/{server_msg_id}` | owner / admin | 204 | OC-3 |
| GET | `/im/conversations/{conversation_id}/export` | owner / admin | `application/x-ndjson` | OC-3 |
| POST | `/im/uploads` | owner / admin | 201 `ImUploadOut` | OC-3 |
| GET | `/im/media/{media_id}` | owner / admin | 图片 | OC-3 |
| POST | `/im/groups` | owner / admin | 201 `ImGroupOut` | OC-3 |
| GET / PATCH / DELETE | `/im/groups/{group_id}` | owner / admin | `ImGroupOut` / 204 | OC-3 |
| POST | `/im/groups/{group_id}/members` | owner / admin | `ImGroupOut` | OC-3 |
| DELETE | `/im/groups/{group_id}/members/{im_user_id}` | owner / admin | 204 | OC-3 |
| PUT | `/im/groups/{group_id}/policy` | owner / admin | `ImGroupPolicyOut` | OC-3 |
| GET | `/im/contacts` | owner / admin | `ImContactList` | OC-3 |
| GET / POST | `/im/contact-groups` | owner / admin | `ImContactGroupList` / 201 `ImContactGroupOut` | OC-3 |
| PATCH / DELETE | `/im/contact-groups/{contact_group_id}` | owner / admin | `ImContactGroupOut` / 204 | OC-3 |
| PUT | `/im/contact-groups/{contact_group_id}/members` | owner / admin | `ImContactGroupOut` | OC-3 |
| GET | `/ai/tool-executions` | viewer（member 及以下只看自己的） | `AiToolExecutionPage` | OC-5 |
| GET | `/ai/tool-executions/{execution_id}` | 同上 | `AiToolExecutionOut` | OC-5 |
| POST | `/ai/tool-executions/{execution_id}/approve`、`/reject` | owner / admin | `AiToolExecutionOut` | OC-5 |

### 2.2 实例接口（只在接入入口）

| 方法 | 路径 | 用途 | 实现 |
|---|---|---|---|
| POST | `/agent-api/v1/secrets/resolve` | 取密钥（取密钥小程序） | OC-2 |
| POST | `/agent-api/v1/im-token` | 取 IM 令牌（启动包装脚本） | OC-3 |
| POST | `/agent-api/v1/heartbeat` | 上报 OpenClaw 与插件版本 | OC-2 |
| POST | `/mcp` | MCP 工具（第 9 节） | OC-5 |
| GET | `/im/ws` | 透传到 OpenIM 消息网关（Caddy forward_auth） | OC-3 |
| POST | `/im/api/{route}` | 接入网关转发 OpenIM REST（第 8 节） | OC-3 |
| GET | `/im/obj/*` | 读取 OpenIM 对象存储里的图片（Caddy forward_auth） | OC-3 |

## 3. 实例（OC-2）

- **字段**：见 `AgentInstanceOut`。
  - `im_user_id` 固定为 `oc_` 加实例 UUID 的 32 位十六进制；管理员的 IM 账号是 `ws_` 加用户 UUID 的十六进制。OpenIM 用户 ID 的长度限制在 OC-3 核实。
  - `scope`：`read` 只读；`read_write` 可以发起写操作，写操作仍要审批。
  - `max_role`：`admin` / `member` / `viewer`，不能是 `owner`。MCP 调用以登记它的 owner（`acting_user_id`）的身份执行，角色取两者中较低的一个。
  - **网络模式登记后不能修改**：换模式要新建实例，因为凭证、入口和接入包都不同。
  - 第一版只有昵称，没有头像（方案第 4.1 节的“头像”以后再做，用默认头像）。
- **状态**：`pending` → `online` ⇄ `offline`；任何时候都能停用（`disabled`），启用后回到 `offline`，等上线回调。
  - `im_ready = false`：OpenIM 注册或加好友失败，后台重试；这时状态停在 `pending`。
- **登记** `POST /agents/instances`：
  - 同一个事务写入实例；随后注册 OpenIM 用户，并与本工作空间**全部 owner / admin** 互加好友（方案只写了“管理员”，工作空间可能有多个管理员）；
  - 加好友前先查好友关系，不是好友才导入（OC-0 F2：重复导入会发一条加好友通知）；
  - **不签发凭证**，凭证只在接入包里。
- **接入包** `POST /agents/instances/{id}/bundle`：
  - 返回 `application/zip`，`Cache-Control: no-store`，文件名 `myworkspace-<实例名>.zip`；
  - 每次调用都签发新的实例令牌，外网模式还签发新的客户端证书；令牌和私钥**只在这个 zip 里出现一次**；
  - `previous=keep_24h`（默认）：已有的有效凭证再保留 24 小时；`revoke_now`：立即吊销。同一种凭证同时最多两份有效，超出时最旧的立即吊销；
  - 内容：`myworkspace.env`（权限 600）、`openclaw.json` 片段（由 `infra/openclaw/openclaw.json.template` 生成）、`bin/myworkspace-secret-resolver`、`bin/myworkspace-openclaw-start`、定时器（`secrets reload` 每 10 分钟，每周重启）、`CHECKLIST.md`；外网模式另有 `tls/client.crt`、`tls/client.key`、隧道配置；
  - `inboundWhitelist` 写入生成时本工作空间全部 owner / admin 和全部实例的 IM ID；管理员或实例变化后要重新生成接入包（OC-2 在实例页提示）。
    这个白名单对一个账号的所有群都生效，不能按群设置，所以“允许接力”由工作台在回调里按群执行（第 10 节）；
  - 已停用的实例返回 409 `AGENT_DISABLED`。
- **凭证**（`AgentCredentialOut`）：
  - 令牌格式 `mwa_` 加 43 个 base64url 字符（32 字节随机数），只存 SHA-256；界面显示前 12 个字符（`token_prefix`）；
  - 绑定登记时的入口：内网令牌在外网入口无效，反之亦然；
  - 外网模式要同时出示属于同一实例的客户端证书，按指纹核对，不依赖 CRL；
  - 记录最近使用时间和来源 IP；
  - `POST .../credentials/{credential_id}/revoke` 立即吊销一份。
- **停用** `POST .../disable`：凭证立即失效、调用 OpenIM `/auth/force_logout`、拒绝取密钥；IM 历史保留，通讯录里显示为已停用。
- **总开关** `PATCH /agents/settings {agents_enabled:false}`：
  - 按工作空间，一键拒绝全部实例：所有实例的 MCP、取密钥、IM 令牌返回 401，并强制下线 IM；
  - 凭证不吊销，重新打开即恢复。
- 写接口都用 `version` 乐观并发，版本不一致返回 409 `VERSION_CONFLICT`（沿用 V1）。

## 4. 密钥库（OC-2）

- **只写不读**：
  - `AgentSecretCreate.value`、`AgentSecretRotate.value` 是 `writeOnly`；
  - 任何响应、日志、审计、校验错误都不含明文（契约测试检查）。
- `kind`：`model_api_key`、`api_token`、`generic`，只用于显示和筛选。
- `name`：工作空间内唯一；`ref_id`（OpenClaw 配置里 SecretRef 的 id）：小写字母开头，只含小写字母、数字、下划线，最多 64 个字符。这是工作台自己的限制，比 OpenClaw 的要求更严。
- **授权** `PUT /agents/instances/{id}/secret-grants`：整体替换，最多 32 条，`ref_id` 在一个实例内唯一。
- **轮换** `POST /agents/secrets/{id}/rotate`：`value_version` 加 1，新值立即生效，旧值不再下发；实例最迟在下一次 `secrets reload`（每 10 分钟）时换上。
- **删除**：同时删除授权；之后实例取用返回 `NOT_FOUND`。
- **取用记录** `GET /agents/secrets/accesses`：实例、`ref_id`、密钥、版本、结果（`ok` / `not_found` / `rate_limited`）、来源 IP、时间。
- **加密密钥可轮换**（方案第 4.3 节）：运维操作，不是接口。
  - 密文带 `key_version`，加密密钥由环境变量提供多个版本；
  - 后台重新加密完成之前保留旧版本；
  - 同时覆盖 V1 的集成凭证。
  - 具体变量名和命令由 OC-2 写进 `docs/deployment.md`。

## 5. IM（OC-3）

### 5.1 身份与范围

| 角色 | OpenIM 用户 ID |
|---|---|
| 工作台后端 | `imAdmin`，只在服务器端用 |
| 管理员 | `ws_<用户 UUID 十六进制>`，第一次打开 IM 时创建 |
| 实例 | `oc_<实例 UUID 十六进制>` |
| 系统通知 | `ws_notice`，发审批卡片和系统提示 |

- OpenIM 是多个工作空间共用的。工作台保证：
  - 单聊只在“管理员 ↔ 本工作空间的实例”之间；
  - 群成员只能是同一工作空间的实例和 owner / admin；
  - 回调里跨工作空间的消息一律拒收（第 10 节）。
- 会话、已读、置顶、免打扰、消息置顶都属于**当前用户 + 工作空间**；分组属于工作空间。
- 只有 owner / admin 能用 IM。用户降级后，接口按角色拒绝；OpenIM 里的好友关系保留，因为浏览器不直连 OpenIM，也没有删除好友的功能。

### 5.2 会话

- 会话 ID 用 OpenIM 的格式：`si_`（单聊）、`sg_`（群聊）、`sn_`（通知）。路径参数按 `^(si|sg|sn)_[A-Za-z0-9_]+$` 校验。
- 列表：置顶的在前（按 `pinned_at` 倒序），其余按 `last_message_at` 倒序；可以按 `kind`、分组、关键字筛选；`unread_total` 不含免打扰的会话。
- `POST /im/conversations {instance_id}`：打开与实例的单聊，已存在时直接返回；实例 IM 未就绪时返回 409 `IM_NOT_READY`。
- `PATCH /im/conversations/{id}`：
  - `pinned` 同步到 OpenIM 的 `isPinned`，`muted` 同步到 `recvMsgOpt=2`；
  - OpenIM 不可用时返回 503 `IM_UNAVAILABLE`，本地不改。
- `POST .../read {seq}`：已读序号只增不减。
- 免打扰：不发通知、不计入 `unread_total` 和顶栏角标，消息照常接收。

### 5.3 消息

- **列表**：按 `sent_at` 倒序，游标分页，`next_cursor` 取更早的消息。
- **`ImMessageOut.kind`**：

| kind | 来源 |
|---|---|
| `text` | contentType 101 |
| `image` | contentType 102 |
| `approval_card` | `ws_notice` 发的、扩展字段带审批 ID 的消息 |
| `system` | 加好友、建群等通知（1201、1501 等），`text` 是工作台生成的说明 |
| `unsupported` | 其他类型：只显示“暂不支持的消息” |

- **发送** `POST .../messages`：
  - 必须带 `Idempotency-Key`；
  - 第一版只支持 `text`（最多 4000 字符）和 `image`（先 `POST /im/uploads`，再带 `upload_id`）；
  - `at_im_ids` 只在群聊有效，最多 20 个；
  - 返回 202，`status` 为 `sent`（OpenIM 已受理）或 `sending`；
  - 结果未知返回 504 `RESULT_UNKNOWN`，**不自动重发**，用同一个幂等键重发以查询结果（沿用 V1 第 7 节）；
  - 被 OpenIM 拒收返回 409 `IM_SEND_REJECTED`；按用户限流，返回 429。
- **状态**：`sending`（本地已记录，OpenIM 还没受理）、`sent`、`failed`、`unknown`。`seq` 在投影补齐之前为 `null`。
- **投影**：回调里的 `seq` 总是 0（OC-0 实测），所以回调只当作“有变化”的提示。Worker 用 `/msg/newest_seq` 和 `/msg/pull_msg_by_seq`（`imAdmin` 令牌）补齐序号和内容。
- **内容一律是纯文本**：前端不渲染 HTML 或 Markdown。

### 5.4 图片

- `POST /im/uploads`：请求体就是图片本身。
  - 只接受 PNG、JPEG、GIF、WebP，不超过 10 MiB（超出返回 413）；
  - 服务端按文件内容核对类型，不只看 `Content-Type`；
  - 返回 `upload_id`；到期（`expires_at`）前没有用于发消息的上传会被删除。
- `ImImage`：
  - 图片在 OpenIM 的对象存储里：给出 `media_id`，经 `GET /im/media/{media_id}` 显示，带登录和会话权限校验；
  - 外部链接：只给 `external_url`，前端**不自动加载**，显示为链接并提示“外部图片”。服务器端也不抓取。
- OpenIM 的对象上传流程在 OC-3 核实（OC-0 ADR 第 8 节）。第一版实例不能发图片（OC-0 F3），所以接入入口只开放 `/im/obj/*` 的读取。

### 5.5 消息置顶

- 每个会话最多 10 条，超出返回 409 `PIN_LIMIT_REACHED`；已置顶的再置顶直接返回那一条。
- 只在工作台记录（`im_message_pins`），不同步到 OpenIM。

### 5.6 群

- `POST /im/groups`：
  - 必须带 `Idempotency-Key`；
  - 群主是当前用户（`groupType=2`）；至少一个实例，最多 50 个实例和 20 个其他 owner / admin；
  - `relay_enabled` 默认打开。
- 改群名和公告、邀请、移出（不能移出群主）、解散（只有群主）。OpenIM 不可用时返回 503。
- **防刷屏设置** `PUT /im/groups/{id}/policy`：
  - `max_agent_streak` 1–50（默认 8），`max_per_minute` 1–60（默认 6）；
  - `paused`：当前是否因连续条数达到上限而暂停，管理员发言后恢复；
  - 规则的执行见第 10 节。

### 5.7 通讯录与分组

- `GET /im/contacts`：本工作空间的全部实例，带状态、所属分组、与当前用户的单聊会话 ID。**没有删除好友**：停用的实例也在通讯录里，显示为 `disabled`。
- 分组：名称在工作空间内唯一，一个实例可以在多个分组里；`PUT .../members` 整体替换。删除分组不影响实例和好友关系。

### 5.8 角标与导出

- `GET /im/badges`：`im_unread`（不含免打扰）和 `pending_approvals`，供顶栏使用。
- `GET .../export`：`application/x-ndjson`，每行一条 `ImMessageOut`，按序号升序；写审计。

## 6. 工具调用与审批（OC-5）

- **记录**：每次 MCP 工具调用都写一条 `ai_tool_executions`，包括读工具（直接是 `succeeded` / `failed` / `denied`）。
- **状态**：

```text
写 / 高危：pending_approval ─┬─ rejected
                             ├─ expired（15 分钟没人处理，默认拒绝）
                             └─ executing ─┬─ succeeded
                                           ├─ failed
                                           └─ unknown（结果未知，不自动重试）
读：succeeded | failed
权限范围或角色不够：denied
```

- **列表** `GET /ai/tool-executions`：可以按 `status`、`risk_level`（可重复）、`instance_id`、`tool_name` 筛选。
  - owner / admin 看本工作空间全部；member 及以下只看 `acting_user_id` 是自己的；
  - `pending_count` 是本工作空间待审批的总数。
- **审批**：
  - `approve` / `reject` 要会话 + CSRF + `If-Match`；
  - `If-Match` 是 GET 返回的 `ETag`，值就是 `version`（例如 `"3"`）。不一致返回 409 `VERSION_CONFLICT`：沿用 V1 的错误码，不用 412，前端统一处理 409；
  - 只有 `pending_approval` 且未过期的能处理，否则 409 `EXECUTION_NOT_PENDING`；
  - **高危操作**要带 `password`（当前用户的密码，`writeOnly`）。没带返回 403 `STEP_UP_REQUIRED`，错误返回 403 `STEP_UP_FAILED`；失败按用户限流；
  - 批准后状态变为 `executing`，用**存下的参数**执行，幂等键是记录 ID（`action_id`）。
- **版本保护**：
  - 修改、完成、删除任务和删除日程时，工作台在创建审批时记下目标的当前版本；
  - 执行时版本已经变化，就以 `failed` / `VERSION_CONFLICT` 结束，不覆盖别人的修改。
- `summary` 由工作台按参数生成，不采用 AI 给的文字。
- `source`：
  - 请求来自哪个会话、哪条消息、由谁 @ 触发；
  - MCP 请求不带会话信息时由工作台推断（`inferred: true`），依据是该实例最近一条收到的私聊或 @ 消息；
  - OpenClaw 是否在 MCP 请求里带会话标识，OC-5 核实。
- **审批卡片**：`ws_notice` 在“审批通知”会话（`kind: notice`）发卡片，同时写进通知中心，类型为 `approval_requested`。卡片按钮直接调用上面的接口。
- **AI 不能批准**：实例凭证调不了管理员接口；聊天里回“同意”不算批准。

## 7. 实例接口 `/agent-api/v1`（OC-2、OC-3）

- **认证**：
  - `Authorization: Bearer <实例令牌>`，外网入口另需客户端证书；
  - **认证失败一律 401 `AGENT_UNAUTHORIZED`**，带 `WWW-Authenticate: Bearer`，不区分原因：令牌错误、已吊销、实例已停用、总开关关闭、入口不对、证书不匹配；
  - 外网入口同一来源认证失败 10 次后封禁 15 分钟，返回 429。
- `POST /secrets/resolve`：
  - 请求 `{"ids": [...]}`，1–32 个 `ref_id`；
  - 返回 `{"values": {...}, "errors": {"<id>": {"code": "NOT_FOUND"}}}`，不区分“不存在”和“没有授权”；
  - `Cache-Control: no-store`；每次取用都写记录；按实例限流（默认每分钟 60 次）；
  - 取密钥小程序再加上 `protocolVersion: 1` 交给 OpenClaw（`infra/openclaw/bin/myworkspace-secret-resolver`）。
- `POST /im-token`：
  - 返回 `ImTokenResponse`：`token`、`im_user_id`、`platform_id`（固定 5）、`expires_at`；
  - `ws_addr` / `api_addr` 只在要覆盖接入包里的地址时给出，否则为 `null`；
  - **签发会踢掉同一实例正在用的旧连接**（OC-0 F1），所以只在启动时调用，每个实例每小时最多 10 次；
  - OpenIM 不可用返回 503 `IM_UNAVAILABLE`。
  - 令牌有效期：OC-0 观察到 OpenIM 默认是 90 天（`expireTimeSeconds=7776000`）。方案要的 30 天能否按次指定，OC-3 核实；不能时调整 OpenIM 的全局配置。
- `POST /heartbeat {openclaw_version, plugin_version}`：返回 `server_time`；更新 `reported_version`、`plugin_version`、`last_seen_at`。由启动包装脚本在启动时调用（OC-2）。

## 8. 接入网关（OC-3）

- **`GET /im/ws`**：
  - Caddy 用 `forward_auth` 先问 API 的 `/internal/gateway/im-auth`，通过后直接透传到 OpenIM 消息网关；
  - API 检查查询参数里的 IM 令牌：属于已启用实例的 `oc_*` 用户、`sendID` 等于令牌的用户、入口与实例一致，外网还要证书指纹一致；
  - IM 令牌按 OpenIM 的 JWT 校验，签名方式 OC-3 核实；不能本地校验时调用 OpenIM `/auth/parse_token`；
  - 访问日志去掉查询参数（令牌在 URL 里）；
  - 消息网关的越权问题靠补丁镜像解决（OC-0 ADR 第 6 节），网关不解析 WebSocket 帧。
- **`POST /im/api/{route}`**：由 API 处理，检查后转发到 OpenIM REST。
  - 白名单就是 OC-0 ADR 第 5 节抓到的接口，每个都检查请求体里的 `userID` 等于令牌的用户：
    - `/conversation/get_incremental_conversations`
    - `/conversation/get_not_notify_conversation_ids`
    - `/friend/get_full_friend_user_ids`
    - `/friend/get_incremental_friends`
    - `/group/get_full_join_group_ids`
    - `/group/get_incremental_join_groups`
    - `/group/get_incremental_group_members_batch`
  - `/user/get_users_info`：`userIDs` 只能是同一工作空间的 IM 用户；
  - `/jssdk/get_active_conversations`、`/jssdk/get_conversations`：protobuf 请求体，解析后再做同样的检查（OC-3）；
  - **其他一律拦截**，包括管理接口、`/friend/delete_friend`、`/conversation/set_conversations`、`/msg/search_msg`、用户注册、建群和踢人：
    - 不在白名单的返回 HTTP 404；
    - 用户 ID 不符返回 HTTP 403；
    - 响应体用 OpenIM 的格式 `{"errCode": 1002, "errMsg": "NoPermissionError", "errDlt": "blocked by My Workspace gateway"}`。插件遇到非 200 的行为在 OC-3 核实；
  - 请求体上限 64 KiB；不转发 `Accept-Encoding`（OC-0 ADR 第 5 节）；去掉响应里的 `Set-Cookie`。
- **`GET /im/obj/*`**：`forward_auth` 同上，只允许 GET（下载图片）；上传路径在第一版拦截（OC-0 F3）。

## 9. MCP 工具（OC-5）

### 9.1 传输与认证

- `POST /mcp`，Streamable HTTP，无状态，JSON 响应（MCP Python SDK 2.3.0 的 `MCPServer`，`streamable_http_app(stateless_http=True, json_response=True)`，OC-0 ADR 第 7 节）。
- 认证同第 7 节。之后走现有的 workspace 范围校验和角色检查，再叠加实例的 `scope`。
- 服务器名 `myworkspace`，**只提供 tools**，不提供 prompts 和 resources（OC-0 M5）。OpenClaw 里的工具 ID 是 `myworkspace__<工具名>`。
- 按实例限流（默认每分钟 120 次调用）。

### 9.2 工具清单

清单、参数和返回在 `app/schemas/ai_tools.py` 的 `TOOLS` 里。参数模型都禁止多余字段；写工具的参数直接复用 V1 的创建模型，校验和 V1 接口一致。

| 风险 | 工具 | 所需 scope | 最低角色 | 返回 |
|---|---|---|---|---|
| 读 | `search`、`list_tasks`、`get_task`、`list_projects`、`calendar_agenda`、`list_bookmarks`、`list_knowledge_sources`、`list_repos`、`list_issues`、`list_pull_requests`、`service_status`、`list_notifications`、`list_automations`、`dashboard_overview`、`get_action_status` | `read` | viewer | 各自的输出模型 |
| 写 | `create_task`（`TaskCreate`）、`update_task`、`complete_task`、`create_calendar_event`（`CalendarEventCreate`）、`create_bookmark`（`BookmarkCreate`） | `read_write` | member | `PendingApproval` |
| 写 | `mark_notification_read` | `read_write` | viewer | `PendingApproval` |
| 高危 | `delete_task`、`delete_calendar_event`、`trigger_automation` | `read_write` | member | `PendingApproval`；批准要二次认证 |

- 实际角色取“登记人的角色”和“实例的 `max_role`”中较低的一个。
- `calendar_agenda` 最多查 31 天；列表类工具 `limit` 最多 50。
- **永远不做成工具**：集成凭证与设置、成员与角色、会话、工作台设置、实例与密钥管理、审计日志。

### 9.3 返回内容

- 只返回需要的字段。
- **来自第三方或用户输入的文字一律放在每个条目的 `untrusted` 对象里**：标题、正文、备注、地址、名称、作者、标签等。结构化字段（ID、枚举、数字、时间）放在外面。契约测试检查读工具的输出模型里没有放错位置的文字字段。
- 唯一的例外：`list_automations` 的参数名。它由管理员登记，受标识符格式限制，不是外部文字。
- 读外部数据的工具带 `freshness`（`ok` / `stale` / `unavailable` / `not_configured`，同 V1 的新鲜度）。
- OpenClaw 还会把整个结果包在 `EXTERNAL_UNTRUSTED_CONTENT` 里（OC-0 M3）。两层防护不互相代替。

### 9.4 写工具与错误

- 写工具和高危工具**立即返回** `PendingApproval {status:"pending_approval", action_id, summary, expires_at, deduplicated}`，此时**没有执行**。
- 同一实例、同一工具、同样参数哈希的待审批请求去重（`deduplicated: true`）。
- 每个实例同时最多 20 条待审批，超出返回 `TOO_MANY_PENDING`。
- **错误**：MCP 结果 `isError: true`，结构化内容 `{"error": {"code", "message"}}`。`code` 的取值见 `ToolError`：
  - `FORBIDDEN_SCOPE`、`FORBIDDEN_ROLE`：同时写一条 `denied` 记录；
  - `NOT_FOUND`、`VALIDATION_FAILED`、`TOO_MANY_PENDING`、`RATE_LIMITED`、`EXTERNAL_UNAVAILABLE`。
- **A14**：AI 发起未经审批的删除，`delete_task` 只返回 `pending_approval`，不执行，并留下记录。

## 10. OpenIM 回调（OC-3）

- **地址**：
  - OpenIM 配置的回调 URL 是 `http://api:8000/internal/openim/<OPENIM_CALLBACK_PATH_TOKEN>`；OpenIM 会在后面加 `/<callbackCommand>`（OC-0 实测）；
  - `path_token` 至少 32 个字符，按常量时间比较，不对返回 404；
  - 只接受来自 `OPENIM_CALLBACK_ALLOWED_CIDRS`（OpenIM 所在的 Docker 网络）的请求；
  - 请求体上限 256 KiB；
  - **回调没有签名**（OC-0 实测），所以只信任回调里的 ID，内容以对账结果为准。
- **应答**：
  - 一律是 HTTP 200 加 OpenIM 的应答格式 `CallbackResponse`；
  - 放行：`{"actionCode":0,"errCode":0,"errMsg":"","errDlt":"","nextCode":0}`；
  - 拒收（只对 before 回调有效）：`errCode` 非 0、`nextCode` 为 1。OC-0 实测插件不会重试。
- **处理的回调**（载荷模型见 `openim_callbacks.py`，字段来自 OC-0 的实际抓包）：

| 命令 | 处理 |
|---|---|
| `callbackBeforeSendSingleMsgCommand` | **同步判断**（200 毫秒内，计数在 Redis）：实例 → 实例拒收 5003；已停用的实例或总开关关闭时拒收 5004；跨工作空间或未知账号拒收 5005；其余放行 |
| `callbackBeforeSendGroupMsgCommand` | **同步判断**：非本工作空间成员 5005；已停用的实例 5004；关闭接力的群里实例 @ 了其他实例 5006；连续条数达到上限 5001（同时由 `ws_notice` 提示“自动接力已暂停，管理员发言后恢复”）；每分钟条数达到上限 5002；管理员（`ws_*`）的消息放行并清零连续计数；`imAdmin` 和 `ws_notice` 的系统消息放行，不清零 |
| `callbackAfterSendSingleMsgCommand`、`callbackAfterSendGroupMsgCommand` | 写入收件箱，键 `msg:<serverMsgID>`；Worker 拉取补齐后写入投影 |
| `callbackAfterUserOnlineCommand`、`callbackAfterUserOfflineCommand` | 写入收件箱，键 `online:` / `offline:` 加 `<userID>:<platformID>:<seq>`；更新实例状态；上线时补一次好友关系 |
| `callbackAfterDeleteFriendCommand` | 写入收件箱；涉及实例与管理员时立即重新导入好友，并发通知 `im_friend_restored` |
| `callbackAfterImportFriendsCommand` | 放行，不处理（工作台自己导入时会触发它，OC-0 F2） |
| 其他命令 | 放行并忽略，按命令计数 |

- **拒收码**：

| errCode | errMsg | 含义 |
|---|---|---|
| 5001 | `RELAY_PAUSED` | 管理员不发言时，实例连续发言达到上限 |
| 5002 | `RELAY_RATE_LIMITED` | 单个实例每分钟条数达到上限 |
| 5003 | `AGENT_DM_FORBIDDEN` | 实例之间单聊 |
| 5004 | `SENDER_DISABLED` | 发送的实例已停用，或总开关已关闭 |
| 5005 | `NOT_ALLOWED` | 跨工作空间、未知账号等 |
| 5006 | `RELAY_DISABLED` | 这个群关闭了接力，实例的消息 @ 了其他实例 |
| 5009 | `POLICY_UNAVAILABLE` | 工作台无法判断（Redis 或数据库不可用）：**拒收，不放行** |

- **工作台 API 不可用时**：OpenIM 的 before 回调请求失败，消息直接失败（方案第 4.5 节，有意如此）。OpenIM 侧的 before 回调要配置为失败时不继续（OC-3 配置并实测）。

## 11. SSE、通知、首页

- **SSE `topics` 增加三个值**：`im`（会话与消息）、`agents`（实例状态）、`ai`（工具调用与审批）。
  - 前端 `useWorkspaceEvents.ts` 的映射表同时补上：`im → ['im']`、`agents → ['agents']`、`ai → ['ai-tool-executions', 'im']`；
  - OC-4 的查询键用这些前缀。
- **通知类型**（`type` 是开放的字符串，前端按普通通知显示不认识的类型）：
  - `approval_requested`、`approval_expired`；
  - `agent_offline`、`agent_credential_expiring`、`agent_im_token_expiring`；
  - `im_friend_restored`、`im_relay_paused`。
- **首页组件**：“AI 快捷提问”改为打开 IM。新组件类型 `im_quick_chat`（配置 `instance_id`，可为空）在 OC-4 与前端一起加入：组件联合类型只增，前端 Registry 同时实现。

## 12. 新错误码

| HTTP | code | 场景 |
|---|---|---|
| 401 | `AGENT_UNAUTHORIZED` | 实例认证失败（任何原因，不区分） |
| 403 | `STEP_UP_REQUIRED` | 批准高危操作没有带密码 |
| 403 | `STEP_UP_FAILED` | 批准高危操作的密码错误 |
| 409 | `AGENT_DISABLED` | 对已停用的实例生成接入包 |
| 409 | `AGENT_SECRET_EXISTS` | 工作空间里已有同名的密钥（`details.existing_id`；OC-2 补充） |
| 409 | `IM_NOT_READY` | 实例的 IM 账号还没就绪 |
| 409 | `IM_SEND_REJECTED` | OpenIM 拒收了这条消息 |
| 409 | `PIN_LIMIT_REACHED` | 会话里置顶的消息已经有 10 条 |
| 409 | `EXECUTION_NOT_PENDING` | 审批的记录不是待审批状态，或已过期 |
| 503 | `IM_UNAVAILABLE` | OpenIM 不可用 |

沿用 V1：`VERSION_CONFLICT`、`IDEMPOTENCY_KEY_REUSED`、`RESULT_UNKNOWN`、`RATE_LIMITED`、`VALIDATION_FAILED`、`NOT_FOUND`、`FORBIDDEN`。

## 13. 留给实现任务核实的事项

| 事项 | 任务 |
|---|---|
| OpenIM 用户 ID 的长度限制；`ws_` / `oc_` 加 32 位十六进制是否可用 | OC-3 |
| IM 令牌有效期能否按次指定；令牌的 JWT 签名方式与 `/auth/parse_token` | OC-3 |
| 插件的 `wsAddr` / `apiAddr` 能否带路径（`/im/ws`、`/im/api`） | OC-2 |
| 插件遇到网关返回非 200 时的行为 | OC-3 |
| `/jssdk/*` 的 protobuf 请求体解析 | OC-3 |
| OpenIM 对象上传流程；实例收到的图片地址怎样经 `/im/obj/*` 访问 | OC-3 |
| `ws_notice` 用通知账号还是普通账号；会话 ID 是否为 `sn_` | OC-3 |
| OpenIM before 回调“失败时不继续”的配置 | OC-3 |
| OpenClaw 是否在 MCP 请求里带会话标识（审批卡片的来源） | OC-5 |
