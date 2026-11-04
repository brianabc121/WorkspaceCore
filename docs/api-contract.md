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
| GET / POST | `/api/v1/workspaces/{workspace_id}/members` | 成员列表 / 添加成员（admin+）：已有账号按邮箱加入；没有账号时同时给 `display_name` 与 `password`（初始密码）创建账号，响应头 `X-Account-Created: true`；没给时 404 `USER_NOT_FOUND`；已是成员 409 `MEMBER_EXISTS`；已有账号又给了密码 409 `USER_EXISTS` | 已实现（T-0064） |
| PATCH / DELETE | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 改角色 / 移除：只能是 admin / member / viewer；admin 只管理 member / viewer，涉及 admin（原来是或改成）只能由 owner；owner 不能被修改或移除；全部写审计（`workspace.member_added`、`workspace.member_role_changed`、`workspace.member_removed`、`user.created`） | 已实现（T-0064） |
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

> **T-0082（2026-11-02）移除了 Vikunja**：项目与任务改为工作台自己的数据，现行接口见下面的「项目与任务（T-0082）」；
> 本节的 Vikunja 部分保留为历史记录。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/integrations` | 集成列表（viewer）/ 新建集成（admin，Token 只写不读；T-0082 起只能是 GitHub） | 已实现（T-0020） |
| PATCH | `/api/v1/integrations/{integration_id}` | 修改集成（admin，乐观并发） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/test` | 测试连接与权限（admin） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/sync` | 排队全量同步（admin，202） | 已实现（T-0021） |
| GET | `/api/v1/projects` | ~~外部项目（投影，带 `sync` 新鲜度）~~ T-0082 改为工作台自己的项目 | 已实现（T-0020，T-0082 改） |
| GET / POST | `/api/v1/tasks` | 任务列表（过滤与分页）/ 创建任务（member，幂等键）；T-0082 起是本地数据 | 已实现（T-0020，T-0082 改） |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改 / 删除（member，幂等键 + `version`）；T-0082 起是本地数据 | 已实现（T-0020，T-0082 改） |
| POST | ~~`/api/v1/webhooks/vikunja/{integration_id}`~~ | Vikunja 回调：只认签名，不用会话与 CSRF（202） | T-0082 已删除 |

新增错误码：`409 IDEMPOTENCY_KEY_REUSED` / `IDEMPOTENCY_IN_PROGRESS` / `OPERATION_NOT_APPLIED` / `INTEGRATION_UNAVAILABLE` / `SYNC_IN_PROGRESS`、
`401 WEBHOOK_SIGNATURE_INVALID`（T-0082 随 Webhook 删除）、`403 EXTERNAL_FORBIDDEN`、`413 PAYLOAD_TOO_LARGE`、`422 EXTERNAL_VALIDATION_FAILED`、
`502 INTEGRATION_UNAUTHORIZED`、`503 EXTERNAL_UNAVAILABLE` / `EXTERNAL_RATE_LIMITED`、`504 RESULT_UNKNOWN`（用同一个幂等键重发以查询结果）。

## 项目与任务（T-0082）

约定见 `docs/decisions/2026-11-02-remove-vikunja.md` 第 3 节（取代 Sprint 3 契约里 Vikunja 的部分）。项目与任务是工作台自己的数据
（单人版：没有负责人、指派、评论）。全部需要 `X-Workspace-Id`；viewer 可读，member 及以上可写，后端校验工作空间范围与角色。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/projects` | 项目列表：默认不含已归档（`include_archived=true` 包含），`q` 按标题搜索，游标分页；每项带 `open_tasks` / `done_tasks` | 已实现（T-0082） |
| POST | `/api/v1/projects` | 新建项目（`title` 最多 120 字，`description` 最多 2000 字）；不需要 Idempotency-Key | 已实现（T-0082） |
| GET | `/api/v1/projects/progress` | 首页“项目进度”：未归档项目的未完成 / 已完成任务数（`limit` 最大 20） | 已实现（T-0064，T-0082 改为本地数据） |
| GET / PATCH / DELETE | `/api/v1/projects/{project_id}` | 详情 / 改名、改说明、归档或恢复（带整数 `version`）/ 删除（`?version=`，连同它的全部任务，审计记下删除的任务数） | 已实现（T-0082） |
| GET / POST | `/api/v1/tasks` | 任务列表（`status` = `open` / `done` / `all`、`project_id`、`priority`、`due_before` / `due_after`、`q`、`sort`、游标分页）/ 创建任务（必须带 `Idempotency-Key`） | 已实现（T-0082） |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改（`Idempotency-Key` + `version`；可以改 `project_id` 移到别的项目）/ 删除（`Idempotency-Key` + `?version=`） | 已实现（T-0082） |

- `ProjectOut`：`id`、`title`、`description`、`archived`、`open_tasks`、`done_tasks`、`version`（整数）、`created_at`、`updated_at`。
- `TaskOut`：`id`、`project_id`、`title`、`description`、`done`、`done_at`、`priority`（0–5）、`due_at`、`labels`、`version`（不透明字符串）、`created_at`、`updated_at`。
  去掉了 `integration_id`、`external_id`、`identifier`、`source_url`、`source_updated_at`、`synced_at`；任务列表不再带 `sync`。
- `labels` 最多 20 个、每个最多 50 个字符，重复的只保留一个；修改任务时只有 `due_at` 可以设为 null（清除截止时间）。
- 版本不一致返回 `409 VERSION_CONFLICT`，`details.current` 是最新的项目或任务。
- 任务的幂等在同一个事务里完成：并发的同一个请求返回 `409 IDEMPOTENCY_IN_PROGRESS`（`Retry-After: 1`）；确定的失败（404、409）也记下来，重放时返回同样的错误。
- 首页 `modules.tasks` 总是 `ok`（本地数据）；新工作空间的 `pending_tasks`、`active_projects` 是 0 而不是 null。
- 删除了 `POST /api/v1/webhooks/vikunja/{integration_id}`；集成只剩 GitHub（`provider` 只能是 `github`），`IntegrationCheck.name` 去掉 `tasks_read`。

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
| POST | `/api/v1/integrations/github/repositories` | 列出 Token 能访问的仓库供网页勾选（admin）：新 Token 只用这一次、不保存；不给 Token 时用已保存的 | 已实现（T-0091） |
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
| GET | `/api/v1/operations/services` | 核心服务、监控组件、集成与 n8n 的状态（没有部署的为 `not_configured`）；V2 另有 `kind = agents` 的 OpenIM 与本工作空间的 OpenClaw 实例（实例只对 owner / admin 列出） | 已实现（T-0035；V2 部分 T-0062） |
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
`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`。标为“已声明”的端点目前返回 `501`，由括号里的任务实现。

**管理员接口**（浏览器；会话 + CSRF + `X-Workspace-Id`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / PATCH | `/api/v1/agents/settings` | 实例总开关（owner） | 已实现（T-0054） |
| GET / POST | `/api/v1/agents/instances` | 实例列表 / 登记（owner；登记不签发凭证） | 已实现（T-0054） |
| GET / PATCH | `/api/v1/agents/instances/{instance_id}` | 详情 / 修改（乐观并发；网络模式不能改） | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/disable`、`/enable` | 停用（凭证失效、IM 强制下线）/ 启用 | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/bundle` | 生成接入包（`application/zip`）并签发新凭证；凭证只在这里出现一次 | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke` | 立即吊销一份凭证 | 已实现（T-0054） |
| PUT | `/api/v1/agents/instances/{instance_id}/secret-grants` | 实例可取用的密钥（整体替换） | 已实现（T-0054） |
| GET / PUT / DELETE | `/api/v1/agents/instances/{instance_id}/ssh` | SSH 自动部署的目标服务器：查看 / 填写或更换（只读取主机公钥、不登录，返回 SHA256 指纹）/ 删除；只支持外网模式 | 已实现（T-0089 B1） |
| POST | `/api/v1/agents/instances/{instance_id}/ssh/confirm` | 确认主机公钥的指纹（与探测结果不一致时 409），之后只接受这把公钥 | 已实现（T-0089 B1） |
| POST | `/api/v1/agents/instances/{instance_id}/ssh/install` | 开始自动安装（202）：密码只用这一次（登录、装专用公钥、需要时 sudo 装 Docker），之后用专用密钥上传接入包、装插件、启动、写 crontab、等实例上线；进度在 GET `/ssh` 的 `job`（steps、脱敏 log）。已装专用公钥且 Docker 可用时可以不填密码；进行中时 409 `AGENT_SSH_JOB_RUNNING`，主机公钥未确认时 409 `AGENT_SSH_HOST_KEY_UNCONFIRMED` | 已实现（T-0089 B2） |
| POST | `/api/v1/agents/instances/{instance_id}/ssh/uninstall` | 卸载（202，任务 kind=uninstall）：只用专用密钥，停止并删除容器、删掉带标记的定时任务；`remove_files` 时删除接入包目录（不可恢复）。没有专用公钥时 409 `AGENT_SSH_KEY_MISSING` | 已实现（T-0089 B3） |
| POST | `/api/v1/agents/instances/{instance_id}/ssh/revoke` | 吊销专用密钥：从服务器的 authorized_keys 删掉专用公钥，再删除工作台保存的私钥；连不上时 503，`force=true` 只删本地并在 `warning` 里给出要手动删除的那一行 | 已实现（T-0089 B3） |
| GET / POST | `/api/v1/agents/secrets` | 密钥列表 / 新增（只写不读） | 已实现（T-0054） |
| GET | `/api/v1/agents/secrets/accesses` | 取密钥记录 | 已实现（T-0054） |
| PATCH / DELETE | `/api/v1/agents/secrets/{secret_id}` | 改名称与说明 / 删除 | 已实现（T-0054） |
| POST | `/api/v1/agents/secrets/{secret_id}/rotate` | 轮换值（旧值不再下发） | 已实现（T-0054） |
| GET | `/api/v1/im/status`、`/api/v1/im/badges` | IM 状态 / 顶栏角标（未读不含免打扰、待审批数） | 已实现（T-0055） |
| GET / POST | `/api/v1/im/conversations` | 会话列表（置顶在前）/ 打开与实例的单聊 | 已实现（T-0055） |
| PATCH | `/api/v1/im/conversations/{conversation_id}` | 置顶 / 免打扰（同步到 OpenIM） | 已实现（T-0055） |
| POST | `/api/v1/im/conversations/{conversation_id}/read` | 已读序号（只增不减） | 已实现（T-0055） |
| GET / POST | `/api/v1/im/conversations/{conversation_id}/messages` | 消息历史 / 发送（必须带 `Idempotency-Key`；结果未知时 `504`，不自动重发） | 已实现（T-0055） |
| GET / POST / DELETE | `/api/v1/im/conversations/{conversation_id}/pins[/{server_msg_id}]` | 消息置顶（每个会话最多 10 条） | 已实现（T-0055） |
| GET | `/api/v1/im/conversations/{conversation_id}/export` | 导出（`application/x-ndjson`） | 已实现（T-0055） |
| POST | `/api/v1/im/uploads` | 上传图片（请求体是图片本身，≤ 10 MiB） | 已实现（T-0055） |
| GET | `/api/v1/im/media/{media_id}` | 显示 OpenIM 对象存储里的图片（外部图片不经这里，前端不自动加载） | 已实现（T-0055） |
| POST | `/api/v1/im/groups` | 建群（必须带 `Idempotency-Key`） | 已实现（T-0055） |
| GET / PATCH / DELETE | `/api/v1/im/groups/{group_id}` | 群详情 / 改群名与公告 / 解散 | 已实现（T-0055） |
| POST / DELETE | `/api/v1/im/groups/{group_id}/members[/{im_user_id}]` | 邀请 / 移出成员 | 已实现（T-0055） |
| PUT | `/api/v1/im/groups/{group_id}/policy` | 防刷屏设置（允许接力、连续条数、每分钟条数） | 已实现（T-0055） |
| GET | `/api/v1/im/contacts` | 通讯录（没有删除好友） | 已实现（T-0055） |
| GET / POST / PATCH / DELETE / PUT | `/api/v1/im/contact-groups[/{contact_group_id}[/members]]` | 分组 | 已实现（T-0055） |
| GET | `/api/v1/ai/tool-executions[/{execution_id}]` | 工具调用记录与待审批（member 及以下只看自己的） | 已实现（T-0057） |
| POST | `/api/v1/ai/tool-executions/{execution_id}/approve`、`/reject` | 审批（必须带 `If-Match`；高危操作要重新输入密码） | 已实现（T-0057） |

**实例接口**（只在接入入口；`Authorization: Bearer <实例令牌>`，外网另需客户端证书；失败一律 `401 AGENT_UNAUTHORIZED`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | `/agent-api/v1/secrets/resolve` | 取密钥：未授权与不存在都返回 `NOT_FOUND` | 已实现（T-0054） |
| POST | `/agent-api/v1/im-token` | 取 IM 令牌（只在启动时调用：签发会踢掉旧连接） | 已实现（T-0055） |
| POST | `/agent-api/v1/heartbeat` | 上报 OpenClaw 与插件版本 | 已实现（T-0054） |
| POST | `/mcp` | MCP 工具（不进 OpenAPI；工具清单见 `app/schemas/ai_tools.py` 的 `TOOLS`；只接受 POST） | 已实现（T-0057；审批卡片在第 3 批） |
| GET / POST | `/im/ws`、`/im/api/{route}`、`/im/obj/m/{media_id}` | 接入网关（不进 OpenAPI；白名单见 V2 契约第 8 节；取图改为能力地址，见 OC-3 ADR 第 15 节） | 已实现（T-0055） |

- OpenIM 回调 `/internal/openim/{path_token}/{command}` 只走内部网络，不进 OpenAPI（V2 契约第 10 节）。
- 已有接口只增不改：SSE 的 `topics` 增加 `im`、`agents`、`ai`。
- 新增错误码：`409 AGENT_SECRET_EXISTS` / `AGENT_INGRESS_NOT_CONFIGURED`、`401 AGENT_UNAUTHORIZED`、`403 STEP_UP_REQUIRED` / `STEP_UP_FAILED`、`409 AGENT_DISABLED` / `IM_NOT_READY` /
  `IM_SEND_REJECTED` / `PIN_LIMIT_REACHED` / `EXECUTION_NOT_PENDING` / `CONTACT_GROUP_EXISTS`（OC-3 补充）、
  `503 IM_UNAVAILABLE`（V2 契约第 12 节）。

## n8n 登录账号（T-0080；T-0082 去掉了 Vikunja）

约定见 `docs/decisions/2026-11-02-app-logins.md`。都要 `X-Workspace-Id`，只给 owner / admin（member、viewer `403`）；
响应都带 `Cache-Control: no-store`。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/app-logins` | 固定一项（`n8n`；T-0082 前还有 `vikunja`）：用户名、`password_set`、`login_url`、`version`；**不含密码**；没保存时 `configured = false` | 已实现（T-0080） |
| PUT | `/api/v1/app-logins/{app}` | 新建（必须给 `username`）或修改（必须带 `version`）；`password` 不填表示不改 | 已实现（T-0080） |
| POST | `/api/v1/app-logins/{app}/reveal` | 返回明文密码；要 CSRF，每次写审计 `app_login.revealed`；没保存密码时 `404` | 已实现（T-0080） |
| DELETE | `/api/v1/app-logins/{app}?version=` | 删除保存的账号（不影响 n8n 里的账号） | 已实现（T-0080） |

## 知识库（T-0098）

方案见 `docs/v2/KNOWLEDGE_PLAN.md`（人类 2026-11-03 批准），OpenClaw 一侧的核验见 `docs/decisions/2026-11-03-knowledge-k0-verification.md`。
都要 `X-Workspace-Id`；写接口要 CSRF。viewer 可读所有知识库，member 及以上编辑笔记，owner / admin 管理知识库设置、参与的智能体和彻底删除。
笔记的 `version` 是整数，修改、删除、恢复、退回都要带上；不一致时 `409 VERSION_CONFLICT`，`details.current` 是服务器上的最新内容（编辑器据此三方合并）。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/knowledge/spaces[/{space_id}]` | 知识库列表 / 详情：公共库、项目库、智能体库；含篇数、参与的智能体、写入开关、删除是否被暂停 | 已实现（T-0098） |
| PATCH | `/api/v1/knowledge/spaces/{space_id}` | 说明、智能体权限（公共库 `read_write` / `read` / `none`，智能体库 `read` / `none`）、`writes_enabled`、`resume_deletes`（admin） | 已实现（T-0098） |
| PUT / DELETE | `/api/v1/knowledge/spaces/{space_id}/agents/{instance_id}` | 项目库的参与智能体（`read_write` / `read`）（admin） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes` | 笔记列表与搜索：`space_id`、`folder`、`type`、`tag`、`author`（`user` / `agent`）、`q`，游标分页 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes/suggest?q=` | `[[` 补全：按标题、别名找笔记（跨库） | 已实现（T-0098） |
| POST | `/api/v1/knowledge/notes` | 新建（201；同一库标题重复 `409 TITLE_TAKEN`） | 已实现（T-0098） |
| GET / PATCH | `/api/v1/knowledge/notes/{note_id}` | 详情（含出链、反链、被智能体读的次数）/ 修改（可移动到别的库） | 已实现（T-0098） |
| DELETE | `/api/v1/knowledge/notes/{note_id}?version=` | 放进回收站，30 天后 Worker 彻底删除（204） | 已实现（T-0098） |
| POST | `/api/v1/knowledge/notes/{note_id}/restore`、`/confirm`、`/revert` | 从回收站恢复 / 人确认可信 / 退回到某一版（`to_version`） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes/{note_id}/revisions[/{version}]` | 版本历史 / 某一版与上一版的逐行差异 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes/{note_id}/graph?depth=1\|2` | 局部图谱 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/graph` | 全局图谱：笔记、`[[链接]]`、链接到的任务 / 项目 / 书签；`space_id` 可多选，`resources=false` 只要笔记；超过 2000 篇时 `truncated` | 已实现（T-0098） |
| GET | `/api/v1/knowledge/changes` | 变更记录：每次改动（谁、何时、增删行数、理由、能否一键退回）与保护记录（`kind=guard`） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/trash` | 回收站 | 已实现（T-0098） |
| DELETE | `/api/v1/knowledge/trash/{note_id}` | 彻底删除（admin，204） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/agents/{instance_id}` | 智能体主页：自己的库、参与的项目、近 30 天写 / 读 / 被纠正 / 被拦下的次数；`weekly` 是近 8 周的趋势（T-0100） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/spaces/{space_id}/activity` | 近 8 周人与智能体的修改次数（新建、修改、改名、退回），按周一起算（UTC），最早的在前 | 已实现（T-0100） |

**MCP 工具**（`/mcp`，工具清单见 `app/schemas/ai_tools.py`）：`knowledge_search`、`knowledge_get`、`knowledge_list`、`knowledge_brief`（风险级别 `read`），
`knowledge_write`、`knowledge_delete`（风险级别 `knowledge`：**直接执行、不进审批**，人类 2026-11-03 决定；仍要通过实例的读写范围与角色检查）。

- 智能体能读写哪些库：公共库按 `agent_access`；项目库只有参与的智能体；自己的库读写，别人的库按对方的 `agent_access`。项目归档或项目 / 实例已删除的库只读。
- 智能体写入：正文不超过 20 KB（人 256 KB），每个库最多 5000 篇；必须带 `idempotency_key`（同一个键、不同参数 `409 IDEMPOTENCY_KEY_REUSED`）、`reason`，可带 `evidence`；
  不能写项目简报（`BRIEF_HUMAN_ONLY`）和《收到的纠正》（`SYSTEM_NOTE`），不能移动笔记（`MOVE_NOT_ALLOWED`）；与已有笔记同名或标题相近时写入照常，两篇都标上“有冲突”（人编辑后清除），`notices` 里提醒智能体去合并。
- 保护：内容里有密钥 `422 SECRET_DETECTED`（不保存内容，只记规则名）；每个实例每小时最多写 20 次（`429 RATE_LIMITED`）、删 5 次，超过后这个库的删除自动暂停（`403 DELETES_PAUSED`）并通知管理员（`knowledge_delete_paused`）；管理员关了写入时 `403 WRITES_DISABLED`。
- 读到的笔记内容放在 `untrusted` 里；`notices` 提示新的纠正和简报更新。智能体的通用 `search` 工具不搜笔记。
- 全局搜索增加 `type=note`；SSE 增加主题 `knowledge`。
- 新增错误码：`409 TITLE_TAKEN` / `NOTE_DELETED` / `NOT_IN_TRASH`、`422 NOTE_TOO_LARGE` / `SECRET_DETECTED` / `SPACE_FULL` / `MOVE_NOT_ALLOWED` / `SYSTEM_NOTE` / `REVISION_NOT_FOUND`、
  `403 READ_ONLY` / `WRITES_DISABLED` / `DELETES_PAUSED` / `BRIEF_HUMAN_ONLY`。

### 认知对齐与自我提升（T-0100）

方案 3.9、3.10。
- **项目简报已读**：`knowledge_brief` 记下这个实例读到的版本，知识库主页显示谁没读最新版。
  - knowledge_* 工具的 `notices` 在参与的项目有未读简报时提醒先读（最多 3 条）。提醒只带 `project_id` 和版本号，是工作台写的固定文字，不含人填的项目名。
  - MCP `get_task` 的输出增加 `brief`：任务所属项目的简报版本 `version`、这个实例读到的 `read_version`、`unread`；项目没有简报或实例不参与时为 `null`。
- **“现在发给它”**：管理员在项目知识库主页点按钮，前端用已有的 IM 接口，以管理员自己的账号把“去读最新简报”发给那个智能体。
  - 先 `POST /api/v1/im/conversations {instance_id}`，再 `POST /api/v1/im/conversations/{id}/messages`，带 `Idempotency-Key`；同一个智能体、同一版简报重试用同一个键。
  - 没有新接口：接入包的渠道只放行管理员的消息（K-0 核验 3.4），工作台不替管理员自动发。
- **每晚复盘**：
  - 接入包增加 `reflection-prompt.md`（提示词）和 `setup-reflection.sh`，后者用 `openclaw automations add --declaration-key myworkspace-knowledge-reflection` 建 OpenClaw 定时任务：独立会话、不投递，重复运行只更新。
  - SSH 部署的步骤增加 `reflection`，在 `online` 之后执行；建不成记为 `skipped`，不算安装失败，CHECKLIST 给出手工命令。
  - 时间由 `AGENT_REFLECTION_CRON`（默认 `0 22 * * *`）和 `AGENT_REFLECTION_TIMEZONE`（默认 `Asia/Shanghai`）设置，生成接入包时写进脚本。
- **趋势**：`GET /api/v1/knowledge/spaces/{space_id}/activity`（上表）、`AgentKnowledgeOut.weekly`：
  - 每周读自己知识库的次数 `reads_own`；
  - 读项目、公共和别的智能体知识库的次数 `reads_shared`；
  - 写入次数 `writes`。
