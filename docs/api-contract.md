# API 契约

> 状态：Sprint 1 ~ 5 端点已全部实现；V2（OpenClaw、IM、审批）的端点已在 OC-1 声明，实现中。完整规范见 `docs/DEVELOPMENT_SPEC.md` 第 6 节。
> **机器可读的接口以 `services/api/openapi.json` 为准**（`make openapi` 由代码生成，CI 检查一致性），前端据此生成类型。
> Sprint 1 的 Cookie、CSRF、工作空间上下文、错误码等约定见 `docs/decisions/2026-10-30-sprint-1-auth-contract.md`。
> Sprint 2 的仪表盘、Widget Registry、布局、乐观并发与首页聚合约定见 `docs/decisions/2026-10-30-sprint-2-dashboard-contract.md`。
> 前端类型 `apps/web/src/services/schema.d.ts` 由 claude 随契约一起重新生成并提交（D-0005）。

## 通用约定

- 业务接口前缀 `/api/v1`，`Content-Type: application/json`。
- 每个响应都带 `X-Request-ID` 头。请求可以自带 `X-Request-ID`（1–64 位字母、数字、`.`、`_`、`-`），否则由服务端生成。
- 非生产环境提供 OpenAPI：`/api/v1/openapi.json`，文档页 `/api/v1/docs`；生产环境关闭。

## 统一错误格式

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found", "request_id": "req_...", "details": {}}}
```

| HTTP | code |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `CONFLICT` |
| 422 | `VALIDATION_FAILED`（`details.errors` 只含 `loc`、`msg`、`type`，不回显用户输入） |
| 429 | `RATE_LIMITED` |
| 500 | `INTERNAL_ERROR`（不包含异常细节） |
| 501 | `NOT_IMPLEMENTED`（契约已声明、尚未实现） |

Sprint 1 新增的业务错误码（`INVALID_CREDENTIALS`、`CSRF_FAILED`、`SETUP_COMPLETED` 等）见 Sprint 1 契约 ADR 第 7 节。

## 已实现的接口

### `GET /healthz`

进程存活检查，不检查依赖。

- `200 {"status": "ok"}`

### `GET /readyz`

并发检查 PostgreSQL（`SELECT 1`）与 Redis（`PING`），每项超时 `READINESS_TIMEOUT_SECONDS`（默认 2 秒）。

- 全部正常：`200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`
- 任一失败：`503 {"status": "unavailable", "checks": {"database": "ok", "redis": "error"}}`

失败原因只写入日志（记录异常类型），不出现在响应里。

## Sprint 1 端点

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/setup/status` | 是否需要首次安装 | 已实现（T-0010） |
| POST | `/api/v1/setup` | 首次安装（需安装凭据） | 已实现（T-0010） |
| POST | `/api/v1/auth/login` | 登录 | 已实现（T-0010） |
| POST | `/api/v1/auth/logout` | 登出 | 已实现（T-0010） |
| GET | `/api/v1/me` | 当前用户与所属工作空间 | 已实现（T-0010） |
| PATCH | `/api/v1/me` | 修改显示名 | 已实现（T-0011） |
| GET | `/api/v1/workspaces` | 我所属的工作空间 | 已实现（T-0010） |
| GET / PATCH | `/api/v1/workspaces/{workspace_id}` | 工作空间详情（viewer+）/ 设置（admin+） | 已实现（T-0011） |
| GET | `/api/v1/auth/sessions` | 我的会话 | 已实现（T-0011） |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 撤销会话 | 已实现（T-0011） |
| POST | `/api/v1/auth/password` | 修改密码 | 已实现（T-0011） |
| GET | `/api/v1/audit-logs` | 审计日志（需 `X-Workspace-Id`，owner / admin） | 已实现（T-0011） |

请求头约定：写请求带 `X-CSRF-Token`（值取自 `ws_csrf` Cookie）；工作空间范围的请求带 `X-Workspace-Id`。

## Sprint 2 端点

全部需要 `X-Workspace-Id`；viewer 可读，member 及以上可写。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/dashboard/overview` | 首页聚合：默认仪表盘、组件、模块状态与汇总 | 已实现（T-0015） |
| GET / POST | `/api/v1/dashboards` | 仪表盘列表 / 创建（可一并导入组件） | 已实现（T-0015） |
| PATCH | `/api/v1/dashboards/{dashboard_id}` | 改标题、设为首页、保存布局（乐观并发） | 已实现（T-0015） |
| GET / POST | `/api/v1/dashboards/{dashboard_id}/widgets` | 组件列表 / 添加组件 | 已实现（T-0015） |
| PATCH / DELETE | `/api/v1/widgets/{widget_id}` | 修改配置（乐观并发）/ 删除组件 | 已实现（T-0015） |

新增错误码：`409 VERSION_CONFLICT`（`details.current_version`）、`409 LIMIT_EXCEEDED`。

## Sprint 3 端点

约定见 `docs/decisions/2026-10-30-sprint-3-tasks-contract.md`（Vikunja 的核验结果见 `docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`）。
除 Webhook 外都需要 `X-Workspace-Id`；任务的写接口还必须带 `Idempotency-Key`（UUID）。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/integrations` | 集成列表（viewer）/ 新建 Vikunja 集成（admin，Token 只写不读） | 已实现（T-0020） |
| PATCH | `/api/v1/integrations/{integration_id}` | 修改集成（admin，乐观并发） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/test` | 测试连接与权限（admin） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/sync` | 排队全量同步（admin，202） | 已实现（T-0021） |
| GET | `/api/v1/projects` | 外部项目（投影，带 `sync` 新鲜度） | 已实现（T-0020） |
| GET / POST | `/api/v1/tasks` | 任务列表（投影，过滤与分页）/ 创建任务（member，幂等键） | 已实现（T-0020） |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改 / 删除（member，幂等键 + `version`） | 已实现（T-0020） |
| POST | `/api/v1/webhooks/vikunja/{integration_id}` | Vikunja 回调：只认签名，不用会话与 CSRF（202） | 已实现（T-0021） |

新增错误码：`409 IDEMPOTENCY_KEY_REUSED` / `IDEMPOTENCY_IN_PROGRESS` / `OPERATION_NOT_APPLIED` / `INTEGRATION_UNAVAILABLE` / `SYNC_IN_PROGRESS`、
`401 WEBHOOK_SIGNATURE_INVALID`、`403 EXTERNAL_FORBIDDEN`、`413 PAYLOAD_TOO_LARGE`、`422 EXTERNAL_VALIDATION_FAILED`、
`502 INTEGRATION_UNAUTHORIZED`、`503 EXTERNAL_UNAVAILABLE` / `EXTERNAL_RATE_LIMITED`、`504 RESULT_UNKNOWN`（用同一个幂等键重发以查询结果）。

## Sprint 4 端点

约定见 `docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md`。GitHub 与 AFFiNE 的核验结果见
`docs/decisions/2026-10-30-github-rest-and-affine-verification.md`。全部需要 `X-Workspace-Id`。
收藏、知识来源、日程都是本地数据，写入**不需要** `Idempotency-Key`。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/knowledge/sources` | AFFiNE 外部地址（viewer 查看 / admin 配置；每个工作空间 1 个） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/knowledge/sources/{source_id}` | 修改（改地址后收藏的链接随之改）/ 删除（连同全部收藏） | 已实现（T-0027） |
| GET | `/api/v1/knowledge/resources` | 文档收藏列表（私有收藏只返回自己的；可按关联的任务过滤） | 已实现（T-0027） |
| POST | `/api/v1/knowledge/bookmarks` | 收藏 AFFiNE 文档（member；链接只做字符串校验，不访问 AFFiNE） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/knowledge/bookmarks/{bookmark_id}` | 修改（乐观并发；只有创建者能改可见性）/ 删除 | 已实现（T-0027） |
| GET / POST | `/api/v1/calendar/events` | 区间内的日程（`start`、`end` 必填，最多 92 天）/ 新建（定时或全天） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/calendar/events/{event_id}` | 修改 / 删除（乐观并发） | 已实现（T-0027） |
| POST | `/api/v1/integrations`（`provider: github`） | 新建 GitHub 集成：细粒度 Token（`github_pat_`）+ 要同步的仓库 | 已实现（T-0028） |
| GET | `/api/v1/developer/repos`、`/issues`、`/pulls`、`/runs` | GitHub 投影（带 `sync`；限流时 `reason: rate_limited` 与 `retry_at`） | 已实现（T-0028） |
| GET | `/api/v1/search?q=` | 跨资源搜索（只返回当前用户可见、未删除的资源） | 已实现（T-0029） |

- 已有接口只增不改：
  - `IntegrationOut.repositories`、`IntegrationTestResult.repositories`；
  - `SyncState.retry_at`、`ModuleStatus.retry_at`；
  - 首页 `modules` 增加 `calendar`、`knowledge`、`github`；
  - 组件类型增加 `calendar_agenda`、`recent_docs`、`github_activity`。
- 修改集成时，`api_token` 的前缀必须与 provider 一致；`repositories` 只用于 GitHub。不适用时返回 422（已实现）。
- 新增错误码：`409 BOOKMARK_EXISTS`（`details.existing_id`）。收藏链接不合规时返回 `422 VALIDATION_FAILED`，`details.reason` 见契约第 4.2 节。

## Sprint 5 端点

约定见 `docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md`。Prometheus、Grafana、n8n 的核验结果见
`docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`。除 SSE 外都需要 `X-Workspace-Id`。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/operations/services` | 核心服务、监控组件、集成与 n8n 的状态（没有部署的为 `not_configured`） | 已实现（T-0035） |
| GET | `/api/v1/operations/metrics?range=&views=` | 预设指标视图，聚合好的数据点；不接受 PromQL；带 `sync` 与 `grafana_url` | 已实现（T-0035） |
| GET / PATCH | `/api/v1/notifications` | 我的通知（游标分页、`unread_count`）/ 标为已读（`ids` 或 `all: true`） | 已实现（T-0036） |
| GET | `/api/v1/events/stream?workspace_id=` | SSE：只发 `event: invalidate` 失效提示；`Last-Event-ID` 续传，无法续传时 `reset: true`；连接太多时 `429` | 已实现（T-0036） |
| GET / PUT | `/api/v1/automations/connection` | n8n 连接（admin；API Key 与共享密钥只写不读） | 已实现（T-0037） |
| POST | `/api/v1/automations/connection/test` | 测试 n8n 连接（不触发工作流） | 已实现（T-0037） |
| GET / POST | `/api/v1/automations` | 白名单工作流与最近一次执行 / 登记（admin） | 已实现（T-0037） |
| PATCH / DELETE | `/api/v1/automations/{automation_id}` | 修改 / 移出白名单（admin，乐观并发；执行记录保留） | 已实现（T-0037） |
| POST | `/api/v1/automations/{automation_id}/trigger` | 触发（member，策略校验，必须带 `Idempotency-Key`；结果未知时为 `unknown`，不自动重发） | 已实现（T-0037） |
| GET | `/api/v1/automations/{automation_id}/runs` | 执行记录（只有状态与错误摘要） | 已实现（T-0037） |

- 已有接口只增不改：
  - 首页 `modules` 增加 `operations`、`automations`；`summary.unread_notifications` 从 T-0036 起返回实际值；
  - 组件类型增加 `service_health`、`api_latency`、`notifications`；
  - `IntegrationCheck.name`、集成的 `provider` 不加值：n8n 连接单独放在 `/automations/connection`。
- SSE 的事件类型在前端用 `components["schemas"]["InvalidateEvent"]`；开始推送之前的错误是普通 JSON。
- 新增错误码：`409 AUTOMATION_UNAVAILABLE`（`details.reason`）、`409 AUTOMATION_EXISTS`（`details.existing_id`）。

## V2 端点（OpenClaw 实例、密钥库、IM、工具审批）

约定见 `docs/decisions/2026-10-31-v2-oc1-contract.md`（下称“V2 契约”）；上游核验见
`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`。所有端点目前返回 `501`，由括号里的任务实现。

**管理员接口**（浏览器；会话 + CSRF + `X-Workspace-Id`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / PATCH | `/api/v1/agents/settings` | 实例总开关（owner） | 已声明（T-0054） |
| GET / POST | `/api/v1/agents/instances` | 实例列表 / 登记（owner；登记不签发凭证） | 已声明（T-0054） |
| GET / PATCH | `/api/v1/agents/instances/{instance_id}` | 详情 / 修改（乐观并发；网络模式不能改） | 已声明（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/disable`、`/enable` | 停用（凭证失效、IM 强制下线）/ 启用 | 已声明（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/bundle` | 生成接入包（`application/zip`）并签发新凭证；凭证只在这里出现一次 | 已声明（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke` | 立即吊销一份凭证 | 已声明（T-0054） |
| PUT | `/api/v1/agents/instances/{instance_id}/secret-grants` | 实例可取用的密钥（整体替换） | 已声明（T-0054） |
| GET / POST | `/api/v1/agents/secrets` | 密钥列表 / 新增（只写不读） | 已声明（T-0054） |
| GET | `/api/v1/agents/secrets/accesses` | 取密钥记录 | 已声明（T-0054） |
| PATCH / DELETE | `/api/v1/agents/secrets/{secret_id}` | 改名称与说明 / 删除 | 已声明（T-0054） |
| POST | `/api/v1/agents/secrets/{secret_id}/rotate` | 轮换值（旧值不再下发） | 已声明（T-0054） |
| GET | `/api/v1/im/status`、`/api/v1/im/badges` | IM 状态 / 顶栏角标（未读不含免打扰、待审批数） | 已声明（T-0055） |
| GET / POST | `/api/v1/im/conversations` | 会话列表（置顶在前）/ 打开与实例的单聊 | 已声明（T-0055） |
| PATCH | `/api/v1/im/conversations/{conversation_id}` | 置顶 / 免打扰（同步到 OpenIM） | 已声明（T-0055） |
| POST | `/api/v1/im/conversations/{conversation_id}/read` | 已读序号（只增不减） | 已声明（T-0055） |
| GET / POST | `/api/v1/im/conversations/{conversation_id}/messages` | 消息历史 / 发送（必须带 `Idempotency-Key`；结果未知时 `504`，不自动重发） | 已声明（T-0055） |
| GET / POST / DELETE | `/api/v1/im/conversations/{conversation_id}/pins[/{server_msg_id}]` | 消息置顶（每个会话最多 10 条） | 已声明（T-0055） |
| GET | `/api/v1/im/conversations/{conversation_id}/export` | 导出（`application/x-ndjson`） | 已声明（T-0055） |
| POST | `/api/v1/im/uploads` | 上传图片（请求体是图片本身，≤ 10 MiB） | 已声明（T-0055） |
| GET | `/api/v1/im/media/{media_id}` | 显示 OpenIM 对象存储里的图片（外部图片不经这里，前端不自动加载） | 已声明（T-0055） |
| POST | `/api/v1/im/groups` | 建群（必须带 `Idempotency-Key`） | 已声明（T-0055） |
| GET / PATCH / DELETE | `/api/v1/im/groups/{group_id}` | 群详情 / 改群名与公告 / 解散 | 已声明（T-0055） |
| POST / DELETE | `/api/v1/im/groups/{group_id}/members[/{im_user_id}]` | 邀请 / 移出成员 | 已声明（T-0055） |
| PUT | `/api/v1/im/groups/{group_id}/policy` | 防刷屏设置（允许接力、连续条数、每分钟条数） | 已声明（T-0055） |
| GET | `/api/v1/im/contacts` | 通讯录（没有删除好友） | 已声明（T-0055） |
| GET / POST / PATCH / DELETE / PUT | `/api/v1/im/contact-groups[/{contact_group_id}[/members]]` | 分组 | 已声明（T-0055） |
| GET | `/api/v1/ai/tool-executions[/{execution_id}]` | 工具调用记录与待审批（member 及以下只看自己的） | 已声明（T-0057） |
| POST | `/api/v1/ai/tool-executions/{execution_id}/approve`、`/reject` | 审批（必须带 `If-Match`；高危操作要重新输入密码） | 已声明（T-0057） |

**实例接口**（只在接入入口；`Authorization: Bearer <实例令牌>`，外网另需客户端证书；失败一律 `401 AGENT_UNAUTHORIZED`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | `/agent-api/v1/secrets/resolve` | 取密钥：未授权与不存在都返回 `NOT_FOUND` | 已声明（T-0054） |
| POST | `/agent-api/v1/im-token` | 取 IM 令牌（只在启动时调用：签发会踢掉旧连接） | 已声明（T-0055） |
| POST | `/agent-api/v1/heartbeat` | 上报 OpenClaw 与插件版本 | 已声明（T-0054） |
| POST | `/mcp` | MCP 工具（不进 OpenAPI；工具清单见 `app/schemas/ai_tools.py` 的 `TOOLS`） | 未挂载（T-0057） |
| GET / POST | `/im/ws`、`/im/api/{route}`、`/im/obj/*` | 接入网关（不进 OpenAPI；白名单见 V2 契约第 8 节） | 已声明（T-0055） |

- OpenIM 回调 `/internal/openim/{path_token}/{command}` 只走内部网络，不进 OpenAPI（V2 契约第 10 节）。
- 已有接口只增不改：SSE 的 `topics` 增加 `im`、`agents`、`ai`。
- 新增错误码：`401 AGENT_UNAUTHORIZED`、`403 STEP_UP_REQUIRED` / `STEP_UP_FAILED`、`409 AGENT_DISABLED` / `IM_NOT_READY` /
  `IM_SEND_REJECTED` / `PIN_LIMIT_REACHED` / `EXECUTION_NOT_PENDING`、`503 IM_UNAVAILABLE`（V2 契约第 12 节）。
