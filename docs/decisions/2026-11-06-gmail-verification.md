# Gmail API 与 Google OAuth 2.0 核验记录（T-0127）

- 日期：2026-11-06
- 方案：`docs/v2/MAIL_PLAN.md`
- 规则：`docs/AGENTS.md` 第 8 节第 3 条（第三方 API 必须核验官方文档或运行实例）

## 1. 来源

| 来源 | 取得方式 | 版本 / 校验 |
|---|---|---|
| Gmail API discovery 文档 `https://gmail.googleapis.com/$discovery/rest?version=v1` | 2026-11-06 下载 | `name=gmail`、`version=v1`、`revision=20261102`；sha256 `ddd273ee3d800f2166de80ee5f296e0b04fe231054e694bed6c223228c55ada4`（217716 字节） |
| Google OpenID 配置 `https://accounts.google.com/.well-known/openid-configuration` | 2026-11-06 下载 | 授权、令牌、撤销端点与 PKCE 方法 |
| OAuth 2.0 网页服务器流程 `https://developers.google.com/identity/protocols/oauth2/web-server` | 2026-11-06 读取 | — |
| OAuth 2.0 总览（refresh token 失效原因）`https://developers.google.com/identity/protocols/oauth2` | 2026-11-06 读取 | — |
| Gmail API 配额 `https://developers.google.com/workspace/gmail/api/reference/quota` | 2026-11-06 读取 | — |
| Gmail API 错误处理 `https://developers.google.com/workspace/gmail/api/guides/handle-errors` | 2026-11-06 读取 | — |
| Gmail API 发信 `https://developers.google.com/workspace/gmail/api/guides/sending` | 2026-11-06 读取 | — |

（为读取 developers.google.com，本会话的云端环境网络白名单加了这个域名。）

## 2. OAuth 2.0（网页服务器流程）

| 项目 | 核验结果 |
|---|---|
| 端点 | 授权 `https://accounts.google.com/o/oauth2/v2/auth`；令牌 `https://oauth2.googleapis.com/token`；撤销 `https://oauth2.googleapis.com/revoke`（OpenID 配置） |
| PKCE | `code_challenge_methods_supported: ["plain", "S256"]`（OpenID 配置）；工作台用 S256 |
| 授权参数 | `client_id`、`redirect_uri`、`response_type=code`、`scope`（空格分隔）为必填；`access_type` 推荐，取值 `online`（默认）/ `offline`，“offline … instructs the Google authorization server to return a refresh token and an access token the first time that your application exchanges an authorization code”；`state` 推荐，用于防 CSRF；`include_granted_scopes` 可选（增量授权）；`login_hint` 可选；`prompt` 可选，取值 `none` / `consent` / `select_account`，不填时“the user will be prompted only the first time your project requests access” |
| 换令牌 | `POST /token`，表单参数 `client_id`、`client_secret`、`code`、`grant_type=authorization_code`、`redirect_uri`；成功 200，响应字段 `access_token`、`expires_in`、`refresh_token`（只有 `access_type=offline` 时才有）、`refresh_token_expires_in`（只在限时授权时）、`scope`（实际授权的范围，空格分隔）、`token_type`（总是 `Bearer`）；“Your application should ignore any unrecognized fields” |
| 已有 refresh token 时 | 想拿新的 refresh token 要撤销原授权或在授权请求里用 `prompt=consent` |
| 刷新 | `POST /token`，参数 `client_id`、`client_secret`、`refresh_token`、`grant_type=refresh_token`；响应有新的 `access_token`、`expires_in`、`scope`、`token_type`，示例里没有新的 `refresh_token` |
| 错误 | 授权被拒时回调带 `error=access_denied`；换令牌失败 `invalid_grant`（“The supplied authorization code is invalid or in the wrong format”）；管理员限制了服务时 `admin_policy_enforced` |
| 部分授权 | “users may not grant your app access to all of them. Your app must verify which scopes were actually granted … disabling the features that rely on those denied scopes” |
| refresh token 失效原因 | 用户撤销；6 个月没用；用户改了密码且令牌含 Gmail 范围；超过已授权令牌数上限；限时授权到期；管理员把服务设为受限（`admin_policy_enforced`）；外部用户类型、发布状态为 “Testing” 的项目 refresh token 7 天过期；同一 Google 账号同一 OAuth 客户端最多 100 个 refresh token，超过时最旧的自动失效 |
| 撤销 | `POST https://oauth2.googleapis.com/revoke?token={token}`（访问令牌或 refresh token 都可以；撤销访问令牌时对应的 refresh token 也撤销）；成功 200，失败 400 加错误码；生效可能有延迟 |

## 3. Gmail API v1（discovery 文档）

根地址 `https://gmail.googleapis.com/`；`userId` 可以用 `me` 表示已授权的用户。

| 方法 | HTTP | 需要的范围（取我们申请的） | 用到的参数 / 字段 |
|---|---|---|---|
| `users.getProfile` | `GET gmail/v1/users/{userId}/profile` | `gmail.readonly` | `emailAddress`、`messagesTotal`、`threadsTotal`、`historyId` |
| `users.labels.list` | `GET gmail/v1/users/{userId}/labels` | `gmail.readonly` | `labels[].id/name/type/messagesUnread` |
| `users.messages.list` | `GET gmail/v1/users/{userId}/messages` | `gmail.readonly` | `q`（与 Gmail 搜索框同样的语法）、`labelIds`、`maxResults`（默认 100，最大 500）、`pageToken`、`includeSpamTrash`；响应 `messages[]`（只有 `id`、`threadId`）、`nextPageToken`、`resultSizeEstimate` |
| `users.messages.get` | `GET gmail/v1/users/{userId}/messages/{id}` | `gmail.readonly` | `format` 取 `minimal` / `full`（默认）/ `raw` / `metadata`；`metadataHeaders`；`Message`：`id`、`threadId`、`labelIds`、`snippet`、`internalDate`（毫秒）、`payload`（`MessagePart`：`partId`、`mimeType`、`filename`、`headers[]{name,value}`、`body{size,data(base64url),attachmentId}`、`parts[]`） |
| `users.threads.get` | `GET gmail/v1/users/{userId}/threads/{id}` | `gmail.readonly` | `format`、`metadataHeaders`；`Thread`：`id`、`messages[]`、`snippet` |
| `users.messages.attachments.get` | `GET gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}` | `gmail.readonly` | `MessagePartBody`：`data`（base64url）、`size` |
| `users.messages.send` | `POST gmail/v1/users/{userId}/messages/send` | `gmail.send` | 请求体 `Message`：`raw`（RFC 2822 邮件的 base64url）、`threadId`；响应 `Message`（`id`、`threadId`、`labelIds`） |
| `users.history.list` | `GET gmail/v1/users/{userId}/history` | `gmail.readonly` | 本期不用（M10 以后增量提醒用） |

discovery 文档里的全部 OAuth 范围共 14 个；本方案只申请 `https://www.googleapis.com/auth/gmail.readonly` 与 `https://www.googleapis.com/auth/gmail.send`。

## 4. 配额与错误（官方文档）

- 配额：每项目每分钟 1,200,000 单位；**每用户每项目每分钟 6,000 单位**；每项目每天 80,000,000 单位。
- 单次消耗：`getProfile` 1、`labels.list` 1、`history.list` 2、`messages.list` 5、`messages.get` 20（`format` 为 full / raw / html 时 60）、`messages.attachments.get` 20、`threads.get` 40（full 时 120）、`messages.send` 100。
- 错误：400；401（凭证无效，刷新令牌）；403 `dailyLimitExceeded`、`domainPolicy`、`rateLimitExceeded`、`userRateLimitExceeded`（后两者用指数退避重试）；404；429（每用户每日上限，**含发信上限**，“with a retry time”）；500 / 502 / 503 / 504（指数退避重试）。
- 发信上限：与 Gmail 本身的每日发信上限共用；超过后可能好几个小时返回 429；“You can't assume that a 200 response means the email was successfully sent.”
- 发信格式：`raw` 是 base64url 编码的 RFC 2822 邮件；回复要进同一会话时，`Subject` 要一致，`References` 与 `In-Reply-To` 要符合 RFC 2822。

## 5. 实现约束（由上面得出）

- 读请求在 401 时刷新一次令牌再试；403 限流与 429 不在请求内重试，返回 `RATE_LIMITED` 和重试时间；5xx 只对读请求做有限次数退避重试。
- 发信不自动重试；超时 / 连接中断后记为结果未知。
- 列表先 `messages.list` 再逐封 `messages.get?format=metadata`（metadataHeaders：From、To、Cc、Subject、Date），控制在每次最多 50 封，配额约 5 + 50 × 20 = 1005 单位，远低于每分钟 6000。
- 读正文用 `format=full`（60 单位）；会话用 `threads.get`（full 时 120 单位）。

## 6. 验收情况

- **CI（T-0131）**：agents 任务的场景 23 用模拟 Google（`tests/e2e/fake_gmail_server.py`，复用后端测试的 `tests/fake_google.py`，
  请求与响应形状按本记录第 2 ~ 4 节）跑完整流程：管理员设置 OAuth 客户端、经授权回调连接邮箱、两个真实 OpenClaw 实例的
  查询 / 发送开关、甲经模型调 `mail_search`、发送的收件人范围、`mail_send` 发出的 RFC 2822 邮件、`client_key` 不重发、
  发件记录与通知、断开时撤销授权。
- **真实 Google 账号：尚未核验**（2026-11-06）。需要人类在 Google Cloud 建 OAuth 客户端并授权自己的邮箱，按
  `docs/deploy-guide.md` 8.7 的清单手工验收（连接、搜索、读只有 HTML 的邮件、发信与回复进同一会话、智能体在允许的范围里发信、
  断开后 Google 账号的“第三方应用”里授权已移除），结果补在这里。模拟服务与真实 Google 可能的差异：错误响应的细节字段、
  `labels.list` 是否带未读数、发信上限的 429 与 Retry-After。
