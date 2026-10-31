# Sprint 5 接口契约：服务状态、预设指标、通知中心、SSE 事件流、n8n 白名单触发、首页模块

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0034）
- 依据：
  - `docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`（下称“核验 ADR”）；
  - 规范第 4、5.1（第 14 项）、6.1 ~ 6.4、7.4、7.5、8.2、8.3、10 节；
  - 第 8 节项目规则 7、9；验收场景 A05、A16；Sprint 5 DoD。
- 实现：
  - 后端：T-0035（运维与指标）、T-0036（通知与 SSE）、T-0037（n8n）；
  - 前端：T-0038（运维页、ServiceHealth / ApiLatency 组件）、T-0039（通知中心、SSE 客户端、自动化页、Notifications 组件）。

## 1. 原则

- **浏览器只拿到 BFF 的结果**（Sprint 5 DoD）：
  - 不接受也不返回 PromQL；
  - 不返回 Prometheus、Grafana、n8n 的内部地址（n8n 的 `base_url` 只给 admin 看），也不返回任何凭据；
  - n8n 的 API Key 与 Webhook 共享密钥只写不读。
- **只增不改**：Sprint 1 ~ 4 已有的请求 / 响应字段与枚举值都不变。具体是：
  - `IntegrationCheck.name`、`IntegrationOut.provider` 这类前端按枚举索引的类型不加值。n8n 连接因此不作为 `/integrations` 的一个 provider，单独放在 `/automations/connection`（第 6.1 节）。
  - `DashboardOverview.modules` 是按名字索引的字典，增加键不影响已有代码。
  - 组件类型由前端自己的 Registry 决定，增加类型只扩大请求体的联合类型。
- 与已有接口一致：
  - 都是工作空间范围的接口，用 `X-Workspace-Id`（SSE 例外，见第 5 节）；
  - viewer 可读，写接口要 CSRF；
  - 读外部数据带新鲜度（`SyncState`）；
  - 未实现的端点运行时返回 501，但不写进 OpenAPI。
- **幂等**：
  - 只有触发 n8n 工作流（写第三方、可能重复执行）要 `Idempotency-Key`，语义同 Sprint 3 契约第 7 节。
  - 白名单与连接配置是本地数据，不需要幂等键，用乐观并发（`version`）。

## 2. 端点总览

| 方法 | 路径 | 角色 | 成功响应 | 实现 |
|---|---|---|---|---|
| GET | `/api/v1/operations/services` | viewer | 200 `ServiceStatusList` | T-0035 |
| GET | `/api/v1/operations/metrics?range=&views=` | viewer | 200 `MetricsOut` | T-0035 |
| GET | `/api/v1/notifications?unread_only=&cursor=&limit=` | viewer（只看自己的） | 200 `NotificationPage` | T-0036 |
| PATCH | `/api/v1/notifications` | viewer（只改自己的） | 200 `NotificationMarkResult` | T-0036 |
| GET | `/api/v1/events/stream?workspace_id=` | viewer | 200 `text/event-stream` | T-0036 |
| GET | `/api/v1/automations/connection` | admin | 200 `AutomationConnectionOut` | T-0037 |
| PUT | `/api/v1/automations/connection` | admin | 200 `AutomationConnectionOut` | T-0037 |
| POST | `/api/v1/automations/connection/test` | admin | 200 `IntegrationTestResult` | T-0037 |
| GET | `/api/v1/automations` | viewer | 200 `AutomationPage` | T-0037 |
| POST | `/api/v1/automations` | admin | 201 `AutomationOut` | T-0037 |
| PATCH | `/api/v1/automations/{automation_id}` | admin | 200 `AutomationOut` | T-0037 |
| DELETE | `/api/v1/automations/{automation_id}?version=` | admin | 204 | T-0037 |
| POST | `/api/v1/automations/{automation_id}/trigger` | member（经策略校验） | 202 `AutomationRunOut` | T-0037 |
| GET | `/api/v1/automations/{automation_id}/runs` | viewer | 200 `AutomationRunPage` | T-0037 |

## 3. 运维

### 3.1 服务状态 `GET /operations/services`

- `items` 的顺序固定：
  - 核心服务：`api`、`worker`、`postgres`、`redis`（`kind: core`）；
  - 监控：`prometheus`、`grafana`（`kind: monitoring`）；
  - 当前工作空间的集成：`integration:<集成 UUID>`（`kind: integration`，`provider` 为 `vikunja` / `github`）；
  - n8n 连接：`n8n`（`kind: integration`，`provider: n8n`）。
- 没有部署或没有配置的组件也列出来，`status: not_configured`。前端据此显示“未部署”，而不是隐藏或当成故障。
- 状态的含义：

| status | 含义 |
|---|---|
| `ok` | 最近一次检查通过 |
| `degraded` | 可用但有问题：被限流、同步落后（stale）、部分检查失败 |
| `down` | 不可用：连接失败、超时、Token 被拒绝 |
| `unknown` | 还没检查过（例如刚启动） |
| `not_configured` | 没有部署、没有配置，或者集成已停用（`reason: disabled`） |

- 检查方式（由 T-0035 实现）：
  - `api` 由响应本身证明可用；
  - `postgres` / `redis` 与 `/readyz` 相同；
  - `worker` 看它定期写入的心跳；
  - Prometheus / Grafana 用各自的 HTTP 接口检查可达（核验 ADR 第 3、4 节）；
  - 集成与 n8n 沿用集成状态与最近一次同步的结果，不在这个请求里调用第三方。
- 检查结果有短时间缓存，每项检查都有超时。一项检查失败只影响那一项，接口本身照常返回 200。
- `reason` 是机器可读的值，例如 `timeout`、`unreachable`、`rate_limited`、`token_rejected`、`not_synced`、`disabled`，不含地址、Token、外部响应原文。
- **健康告警**（S5-02）：
  - 一项服务从正常变为 `down`、或从 `down` 恢复时，给工作空间的 owner / admin 各发一条通知（`service_down` / `service_recovered`，第 4 节）；
  - 同一项服务的同一种变化在一段时间内只发一次，避免反复跳变时刷屏。

### 3.2 预设指标 `GET /operations/metrics`

- 查询参数：
  - `range`：`1h`（默认）/ `6h` / `24h` / `7d`；
  - `views`：可重复，默认返回全部预设视图。
- 不认识的视图 ID 或时间范围返回 422；不认识的其他查询参数被忽略。**没有任何参数能携带 PromQL**。
- 视图（查询按核验 ADR 第 3.3 节，由 T-0035 定稿）：

| id | unit | 序列 |
|---|---|---|
| `request_rate` | `requests_per_second` | 单序列（`label` 为空字符串） |
| `latency_p50` / `latency_p95` / `latency_p99` | `seconds` | 单序列 |
| `error_rate` | `ratio`（5xx 占比，0–1） | 单序列 |
| `services_up` | `count`（1 为 up，0 为 down） | 每个抓取目标一条，`label` 为服务名 |

- 步长随范围固定：`1h` → 60 秒，`6h` → 300 秒，`24h` → 900 秒，`7d` → 3600 秒（`step_seconds`）。
- `MetricPoint.value` 为 `null` 表示没有数据或无法计算（例如没有请求时的 5xx 率），**不以 0 冒充**（规范第 16 节 A17 的原则）。
- 新鲜度 `sync`：
  - 没有部署 Prometheus：`not_configured`，`views` 为空；
  - 查询成功：`ok`，`as_of` 为查询时间；
  - Prometheus 不可达或超时：有之前的结果时返回那份结果，`sync: stale`、`as_of` 为那次查询的时间；没有时 `views` 为空、`sync: unavailable`。
- CPU / 内存视图不在 V1 契约里。核验 ADR 第 6 节：没有核验 exporter。以后加入时增加 `MetricViewId` 的取值，属于只增不改。

### 3.3 Grafana 外链

- `MetricsOut.grafana_url`：部署时配置的 Grafana 仪表盘地址（浏览器访问的地址），没有配置时为 `null`。
- 前端只把它当外链打开（新标签页，`rel="noopener noreferrer"`），不内嵌（核验 ADR 第 4 节）。
- 工作台不调用 Grafana API，不保存 Grafana 的账号或 Token。

## 4. 通知中心

- **范围**：通知属于“用户 + 工作空间”。
  - 列表与标记都只作用于当前用户在 `X-Workspace-Id` 指定的工作空间里的通知；
  - 用别人的通知 ID 标记，按不存在处理：不计入 `updated`，也不报错，避免泄露 ID 是否存在。
- **字段**（规范 5.1 第 14 项）：`id`、`type`、`severity`（`info` / `warning` / `error`）、`title`、`body`、`link`、`read_at`、`created_at`。
  - `type` 是开放的字符串，例如 `service_down`、`service_recovered`、`integration_unauthorized`、`integration_rate_limited`、`automation_failed`、`automation_succeeded`。前端遇到不认识的类型按普通通知显示。
  - `title` / `body` 是纯文本，前端按文本显示，不解析 HTML / Markdown；不含 Token、第三方 payload 原文。
  - `link` 只会是工作台内部路径（以 `/` 开头），例如 `/operations`、`/automations`；前端只在应用内跳转。
- **列表**：
  - 按 `created_at` 倒序，游标分页（`limit` 1–100，默认 30）；
  - `unread_only=true` 只返回未读；
  - `unread_count` 与首页 `summary.unread_notifications` 一致（Sprint 2 起为 `null`，T-0036 起返回实际值）。
- **标为已读** `PATCH /notifications`：
  - 请求体是 `{"ids": [...]}`（1–100 个）或 `{"all": true}`，二者必须且只能给一个；
  - 已经是已读的不计入 `updated`；
  - 响应带新的 `unread_count`。
- V1 不提供删除；保留期由 T-0036 决定并写进部署文档。

## 5. SSE 事件流 `GET /events/stream`

- **只发失效提示**（规范第 4 节）：事件不带数据本身，客户端收到后按 `topics` 重新拉取对应的接口。
- **鉴权与范围**：
  - 浏览器的 `EventSource` 不能带自定义请求头，所以用查询参数 `workspace_id` 指定工作空间；
  - 会话 Cookie 照常校验，必须是该工作空间的成员（任何角色）；
  - 只推送这个工作空间的事件。
  - 校验在开始推送之前完成：失败时返回普通的 JSON 错误（401 / 404 / 422），而不是一个空的事件流。
- **事件格式**（`text/event-stream`，按 FastAPI 0.162 自带的 `EventSourceResponse` 编码）：

```text
id: 1730340000123-0
event: invalidate
data: {"workspace_id":"…","topics":["tasks","overview"],"reset":false,"at":"2026-10-31T02:00:00Z"}

: ping
```

  - 每个事件都是 `event: invalidate`，`data` 是 `InvalidateEvent` 的 JSON。
  - `topics` 取值：`overview`、`tasks`、`projects`、`knowledge`、`calendar`、`developer`、`integrations`、`notifications`、`operations`、`automations`。以后可能增加取值，前端遇到不认识的忽略即可。
  - 以冒号开头的行是心跳：连接空闲 15 秒就会发一次（锁定的 FastAPI 0.162.0 中 `fastapi/sse.py` 的 `_PING_INTERVAL = 15.0`、`KEEPALIVE_COMMENT = ": ping"`），用来保持代理连接。
- **重连与 `Last-Event-ID`**：
  - `id` 是不透明的字符串，客户端不要解析；
  - 浏览器重连时自动带上 `Last-Event-ID`，服务端补发之后的事件；
  - 无法补发时（事件已过期、服务重启、`id` 不认识），先发一条 `reset: true` 的事件，`topics` 为全部取值，客户端应重新拉取当前页面的全部数据。
- **不是可靠队列**（规范第 10 节、A16）：
  - SSE 只用来更快地刷新。正确性仍由各接口的数据与周期对账保证。
  - 客户端在以下情况都要重新拉取当前页面的数据：重连成功后、页面从后台回到前台时，以及无法使用 SSE 时（降级为原有的手动刷新 / 定时刷新）。
- 补发的范围、每个用户的连接数上限、Caddy 的超时配置由 T-0036 决定并写进部署文档。

## 6. 自动化（n8n）

### 6.1 n8n 连接（每个工作空间一个，admin）

- `GET /automations/connection`：没有配置时 `configured: false`。
- `PUT /automations/connection`：新建或修改。
  - 新建时必须给 `base_url`、`api_key`、`trigger_secret`，不给 `version`；
  - 修改时必须给 `version`（乐观并发），只发送要改的字段；
  - 至少要改一项，多余字段返回 422。
- 字段：
  - `base_url`：工作台访问 n8n 的地址，按集成的地址规则校验（不允许 userinfo、查询串、危险 scheme、云 metadata 地址；主机必须在 `INTEGRATION_ALLOWED_HOSTS` 里，与 Vikunja 相同）。
  - `public_url`：浏览器打开 n8n 编辑器的地址，不填时与 `base_url` 相同。
  - `api_key`：n8n 的 API Key，只勾选 `workflow:list`、`workflow:read`、`execution:list`、`execution:read` 四个 scope（核验 ADR 第 5.3 节）。
  - `trigger_secret`：Webhook 节点 Header Auth 凭据的值，工作台触发时放进请求头 `X-Workspace-Trigger`（核验 ADR 第 5.2 节）。
  - `enabled: false`：停用，不读取执行记录，拒绝触发。
- 两个密钥都加密保存（同 Vikunja / GitHub 凭据），只写不读：响应里只有 `api_key_set`、`trigger_secret_set`；422 错误不回显输入。
- `POST /automations/connection/test` 返回 `IntegrationTestResult`。`checks` 只用已有的三个名字：
  - `reachable`：`/healthz/readiness`；
  - `version`：实例版本，`tested_version` 为 `2.45.7`，不一致只给警告；
  - `token`：API Key 能列出工作流与执行。
  - 测试不会触发任何工作流，也不会尝试任何写操作。所以测试无法发现权限过大的 Key：部署文档要写明只勾选上面四个 scope。

### 6.2 白名单工作流

- 登记（admin）：
  - `name`；
  - `workflow_id`：n8n 的工作流 ID，字母与数字；
  - `webhook_path`：Webhook 节点的 path，只能是字母、数字、`-`、`_`，不能是完整网址；
  - `params`：触发参数的声明，最多 20 个；
  - `enabled`。
- 登记时工作台到 n8n 核对工作流存在（`GET /workflows/{id}`），不存在返回 422（`details.reason: workflow_not_found`）。
  - 未激活的工作流可以登记，`workflow_active: false`，但不能触发。
  - 同一个 `webhook_path` 或 `workflow_id` 在同一工作空间只能登记一次（`409 AUTOMATION_EXISTS`）。
- 参数声明 `ParamSpec`：
  - `name` 为小写字母开头的标识符；`type` 为 `string` / `number` / `boolean`；
  - `required`；
  - `max_length` 与 `choices` 只用于 `string`；
  - `sensitive: true` 的参数值不写进审计与执行记录。
- 列表 `GET /automations`：每项带 `last_run`（最近一次执行）。`connection` 是 n8n 连接的新鲜度：未配置为 `not_configured`，Key 失效为 `unauthorized`。
- 删除只移出白名单，执行记录保留，可在审计里追溯。

### 6.3 触发 `POST /automations/{id}/trigger`

- 角色：member 及以上。服务端按以下**策略**逐项校验，任何一项不满足都不发请求（项目规则 9）：
  1. 白名单条目存在且 `enabled`；
  2. n8n 连接已配置、`enabled`、状态正常；
  3. 最近一次读到的工作流是激活的（`workflow_active` 为 `true`）；
  4. `params` 只包含声明过的参数，类型、长度、可选值都符合，必填的都有。
- 规则 1 ~ 3 不满足时返回 `409 AUTOMATION_UNAVAILABLE`，`details.reason` 为 `disabled` / `not_configured` / `connection_unavailable` / `workflow_inactive`；规则 4 不满足时返回 422。
- 策略拒绝的触发也写审计（第 9 节）。
- V1 白名单里的工作流都视为普通风险：member 可以直接触发。需要逐次审批的高风险自动化放到 Sprint 6，与 AI 工具审批一起做（规范 9.3）。
- **必须带 `Idempotency-Key`**（UUID），语义同 Sprint 3 契约第 7 节：
  - 同一个键、同样的请求：原样返回第一次的执行记录，不会再触发一次；
  - 同一个键、不同的请求：`409 IDEMPOTENCY_KEY_REUSED`；
  - 第一次请求还在处理：`409 IDEMPOTENCY_IN_PROGRESS`。
- 工作台调用 `POST <base_url>/webhook/<webhook_path>`：
  - 带 `X-Workspace-Trigger`；
  - 请求体只有校验后的参数，外加 `workspace_run_id`（执行记录的 ID），方便在 n8n 里对应；
  - 有超时，**不自动重试**：重复执行工作流的代价不可控。
- 响应 202 与一条执行记录：
  - n8n 确认收到：`status: queued`；
  - 超时、连接中断等结果未知：`status: unknown`。未知的执行记录由对账确认（第 6.4 节），工作台不会自动重发。
  - n8n 明确拒绝时不创建执行记录，并通知 admin：
    - 403（共享密钥不对）：`502 INTEGRATION_UNAUTHORIZED`；
    - 404（路径不存在或工作流未激活）：`409 AUTOMATION_UNAVAILABLE`，`details.reason: webhook_not_found`。
  - n8n 不可用（连接失败且确定没有送达、503）：返回 `503 EXTERNAL_UNAVAILABLE`。

### 6.4 执行记录

- `AutomationRunOut`：
  - `status`：`queued` / `running` / `success` / `error` / `canceled` / `unknown`；
  - `external_execution_id`：n8n 的执行 ID，还没对应上时为 `null`；
  - `triggered_by`、`error_summary`、`started_at`、`finished_at`、`created_at`。
- n8n 执行状态的映射（核验 ADR 第 5.3 节的枚举）：

| n8n | 工作台 |
|---|---|
| `new`、`waiting` | `queued` |
| `running` | `running` |
| `success` | `success` |
| `error`、`crashed` | `error` |
| `canceled` | `canceled` |
| `unknown` | `unknown` |

- worker 周期读取 n8n 的执行列表（只读 Key，不带 `includeData`），更新未结束的记录。触发与执行记录的对应方式由 T-0037 实测后确定（核验 ADR 第 5.2 节标为未核验）。
- `unknown` 的记录对账之后：
  - 找到对应的执行：更新为实际状态；
  - 超过对账窗口仍找不到：保持 `unknown`，`error_summary` 写明“无法确认是否执行”，并通知触发者。用户可以带新的幂等键再次触发。
- `error_summary`：纯文本，最多 500 字，只取 n8n 的错误信息（读取方式由 T-0037 实测，核验 ADR 第 5.3 节），**不保存第三方的完整 payload**（规范 7.5）。执行记录里也不保存触发参数。
- 执行结束时给触发者发通知：失败为 `automation_failed`，成功为 `automation_succeeded`。

## 7. 首页模块与组件

### 7.1 modules

`DashboardOverview.modules` 增加两个键，规则沿用 Sprint 2 ~ 4（单个模块失败只影响对应组件）：

| 键 | 规则 |
|---|---|
| `operations` | 核心服务都正常时 `ok`；有核心服务为 `down` 时 `unavailable`（`reason: service_down`）；只有可选组件或集成有问题时仍为 `ok`，具体看运维页 |
| `automations` | 没有 n8n 连接时 `unavailable` + `reason: not_configured`；否则按 `AutomationPage.connection` 映射（`stale` / `unavailable` / `unauthorized`） |

`summary.unread_notifications` 从 T-0036 起返回实际的未读数（之前为 `null`）。`summary` 的字段不变。

### 7.2 新的组件类型（规范 8.2）

| widget_type | config | 数据来源 |
|---|---|---|
| `service_health` | `limit`（1–20，默认 8） | `GET /operations/services` |
| `api_latency` | `range`（`1h` 默认 / `6h` / `24h`）、`quantile`（`p50` / `p95` 默认 / `p99`） | `GET /operations/metrics?range=&views=latency_<quantile>` |
| `notifications` | `limit`（1–20，默认 5）、`unread_only`（默认 `true`） | `GET /notifications?unread_only=&limit=` |

- 组件自己调用列表接口，首页聚合只提供模块状态（规范 6.2）。
- 收到 SSE 的对应 `topics` 后重新拉取：`operations`、`notifications`。

## 8. 权限

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看服务状态、指标、白名单与执行记录；接收 SSE | ✓ | ✓ | ✓ | ✓ |
| 查看、标记自己的通知 | ✓ | ✓ | ✓ | ✓ |
| 触发白名单工作流 | ✓ | ✓ | ✓ | — |
| 配置 n8n 连接、测试连接；登记 / 修改 / 移出白名单 | ✓ | ✓ | — | — |

## 9. 审计日志

沿用 Sprint 1 契约第 11 节。metadata 只含 ID、字段名与状态，**不含** 参数值、密钥、地址、错误正文。新增事件：
- `automation_connection.created` / `updated` / `tested`；
- `automation.created` / `updated` / `deleted`；
- `automation.triggered`：记录白名单 ID、执行记录 ID、参数名（不含值）、结果（`queued` / `unknown` / 拒绝的原因）。这是 Sprint 5 DoD“触发自动化有审计记录”的依据；策略校验拒绝的触发也记录。
- 通知的已读不记审计。

## 10. 新增错误码

| HTTP | code | 场景 |
|---|---|---|
| 409 | `AUTOMATION_UNAVAILABLE` | 白名单停用、n8n 未配置或不可用、工作流未激活、Webhook 路径不存在（`details.reason` 见第 6.3 节） |
| 409 | `AUTOMATION_EXISTS` | 同一个 `webhook_path` 或 `workflow_id` 已登记（`details.existing_id`） |

沿用：
- `409 VERSION_CONFLICT`、`409 IDEMPOTENCY_KEY_REUSED`、`409 IDEMPOTENCY_IN_PROGRESS`；
- `422 VALIDATION_FAILED`（含 `details.reason: workflow_not_found`）；
- `502 INTEGRATION_UNAUTHORIZED`（n8n 拒绝 API Key 或共享密钥）、`503 EXTERNAL_UNAVAILABLE`。

## 11. 不在本契约内

- 任意 PromQL、告警规则配置（留在 Grafana，规范 7.4）、内嵌 Grafana；
- CPU / 内存指标（没有核验 exporter）；
- 详细请求日志的查询界面（S5-05 只要求保留 7 天与无敏感信息）；
- 通知的邮件 / 推送渠道、通知偏好设置、删除通知；
- 在工作台里编辑 n8n 工作流、激活 / 停用工作流（只读 Key 做不到，也不应该做到）；
- 需要审批的高风险自动化（Sprint 6）；
- 多个 n8n 实例。

## 12. 后果

- 后端：
  - T-0035 实现第 3 节与 `operations` 模块，加 `prometheus-client==0.26.0`、`/metrics` 与可选的 `infra/monitoring/`。
  - T-0036 实现第 4、5 节与 `summary.unread_notifications`；通知的表结构与保留期给出 Alembic 迁移；其他模块在写入路径上发布失效事件。
  - T-0037 实现第 6 节与 `automations` 模块；n8n 连接与白名单、执行记录的表结构给出迁移；提供可选的 `infra/n8n/`。
- 前端：
  - T-0038 / T-0039 用重新生成的 `schema.d.ts`。
  - SSE 事件的类型用 `components["schemas"]["InvalidateEvent"]`（openapi-typescript 7 把 `text/event-stream` 的响应生成为 `unknown`）。
  - 同源部署，`EventSource` 不需要 `withCredentials`，Cookie 会自动带上。
- 本 PR 在服务端增加的只有请求形态的校验（第 4、6 节的请求体规则），其余新端点在实现之前返回 501。
