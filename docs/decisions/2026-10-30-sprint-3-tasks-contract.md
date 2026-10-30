# Sprint 3 接口契约：Vikunja 集成、项目与任务、Idempotency-Key 与结果未知、Webhook

- 状态：accepted
- 日期：2026-10-30
- 作者：claude（协作任务 T-0019）
- 依据：`docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`（下称“核验 ADR”）；规范第 4.1、4.2、5.1、6、7、10 节
- 实现：后端 T-0020（集成、Connector、项目 / 任务读写、幂等）与 T-0021（Webhook Inbox、Worker、对账）；前端 T-0022

## 1. 背景与原则

- **Vikunja 是任务主源**（规范 4.2）。工作台只保存外部对象的映射与只读投影（`external_resources`）、幂等记录和本地状态，不把任务复制成自己的主表。
- **读走投影，写走 Connector**：列表与详情读本地投影，快且在 Vikunja 不可用时仍能显示（带新鲜度）；写操作同步调用 Vikunja，成功后更新投影；
  Webhook 与周期对账负责把 Vikunja 原生界面里的改动带回来。
- 不能假设 Vikunja 与 PostgreSQL 在一个事务里（规范 4.1）：写操作靠幂等键、状态记录与“先查询再决定”处理结果未知，绝不盲目重建。
- 所有接口都是工作空间范围（`X-Workspace-Id`），沿用 Sprint 1 的角色与 CSRF 约定；Webhook 端点例外（第 9 节）。

## 2. 端点总览

| 方法 | 路径 | 角色 | 成功响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/v1/integrations` | viewer | 200 `Page[IntegrationOut]` | 集成列表（不含任何凭证） |
| POST | `/api/v1/integrations` | admin | 201 `IntegrationOut` | 新建 Vikunja 集成；保存后状态为 `pending`，需测试连接 |
| PATCH | `/api/v1/integrations/{integration_id}` | admin | 200 `IntegrationOut` | 改名称、地址、Token、启用状态；需要 `version` |
| POST | `/api/v1/integrations/{integration_id}/test` | admin | 200 `IntegrationTestResult` | 测试连接与权限，记录实例版本 |
| POST | `/api/v1/integrations/{integration_id}/sync` | admin | 202 `SyncRequestOut` | 排队一次全量同步 |
| GET | `/api/v1/projects` | viewer | 200 `ProjectPage` | 外部项目（投影） |
| GET | `/api/v1/tasks` | viewer | 200 `TaskPage` | 任务列表（投影），可过滤与分页 |
| POST | `/api/v1/tasks` | member | 201 `TaskOut` | 创建任务；必须带 `Idempotency-Key` |
| GET | `/api/v1/tasks/{task_id}` | viewer | 200 `TaskOut` | 任务详情（投影） |
| PATCH | `/api/v1/tasks/{task_id}` | member | 200 `TaskOut` | 修改任务；必须带 `Idempotency-Key` 与 `version` |
| DELETE | `/api/v1/tasks/{task_id}` | member | 204 | 删除任务；必须带 `Idempotency-Key` 与 `version`（前端先二次确认） |
| POST | `/api/v1/webhooks/vikunja/{integration_id}` | 签名 | 202 `WebhookAccepted` | Vikunja 回调；不用会话与 CSRF |

- 非成员 404、角色不够 403（沿用 Sprint 1）。写接口都要求 CSRF（Webhook 除外）。
- 未实现的端点运行时返回 501，但不写进 OpenAPI（沿用 Sprint 2 契约 ADR 第 1 节）。

## 3. 资源标识

- 项目与任务在工作台里的 `id` 是投影行（`external_resources`）的 **UUID**，路径参数与 `project_id` 都用它。
  `external_id` 形如 `vikunja:task:42` / `vikunja:project:7`，只用于显示与排错；`source_url` 指向 Vikunja 原生界面。
- 与规范 6.3 示例（`"project_id": "vikunja:project:42"`）的偏差：同一工作空间以后可能有多个集成，只带提供方前缀的 ID 会有歧义；
  UUID 也避免了路径里的冒号。外部 ID 仍然完整保留在 `external_id` 里。

## 4. 集成（Integration）

### 4.1 字段

`IntegrationCreate`：`provider`（本 Sprint 只有 `vikunja`）、`name`（1–64）、`base_url`（工作台访问 Vikunja 的地址，例如 `http://vikunja:3456`）、
`public_url`（可选，浏览器打开原生界面的地址，默认同 `base_url`）、`api_token`（只写）、`token_expires_at`（可选，Token 的过期时间，用于到期提醒）。

`IntegrationOut`：`id`、`provider`、`name`、`base_url`、`public_url`、`status`、`token_set`（是否已保存 Token，不返回 Token 本身）、`token_expires_at`、
`tested_version`（`2.7.1`）、`api_version`（`v2`）、`instance_version`（最近一次测试连接时实例报告的版本）、`last_tested_at`、
`sync`（第 10 节的 `SyncState`）、`webhooks`（`mode`：`auto` / `unavailable`；`registered_projects`；`reason`）、`version`、`created_at`、`updated_at`。

`IntegrationUpdate`：`version`（必填）与 `name`、`base_url`、`public_url`、`api_token`、`token_expires_at`、`enabled` 中至少一项。
修改 `base_url` 或 `api_token` 后状态回到 `pending`，需要重新测试连接。

### 4.2 状态

| status | 含义 |
|---|---|
| `pending` | 新建或改了地址 / Token，尚未测试通过 |
| `active` | 测试通过，正常同步 |
| `unauthorized` | Vikunja 返回 401（Token 无效、过期或权限不足，核验 ADR 第 3 节无法区分），需要更换 Token |
| `unavailable` | 连续访问失败（网络、5xx）；恢复后自动回到 `active` |
| `disabled` | 管理员停用；不同步、拒绝写入 |

### 4.3 规则

- V1 每个工作空间每个 provider 最多 **1 个**集成（第 2 个返回 `409 LIMIT_EXCEEDED`）；字段里保留 `integration_id`，以后放开不用改契约。
- `api_token` 必须是 Vikunja API Token（`tk_` 前缀，核验 ADR 第 3 节）；用 Sprint 1 的凭证加密服务保存（用途 `integration_account:<id>`），
  任何接口、日志、审计里都不出现。Webhook 签名密钥由服务端生成（32 字节随机数），同样加密保存、从不返回。
- `base_url` / `public_url`：只允许 `http` / `https`，不能带用户名密码、查询串或片段，可以带路径前缀（保存时去掉末尾的 `/`）。
  **SSRF 防护**：解析到私有、回环、链路本地等地址的主机一律拒绝，除非主机名在部署配置 `INTEGRATION_ALLOWED_HOSTS`（逗号分隔，Compose 默认 `vikunja`）里；
  连接时再按实际解析出的地址检查一次，防 DNS 重绑定。不允许时返回 `422 VALIDATION_FAILED`（`loc` 指向该字段，不回显地址）。
- 测试连接依次检查：能访问 `/api/v2/info`；实例版本；Token 有效（读取一页项目）；任务读权限；是否有注册 Webhook 的权限（没有时 `webhooks.mode = unavailable`，只靠对账同步）。
  结果逐项给出 `checks[]`（名称、是否通过、错误码），不返回外部响应原文。实例版本与 `tested_version` 不同时 `compatible = false` 并在 `warnings` 提示，不阻止使用。

## 5. 项目

`GET /api/v1/projects?integration_id=&include_archived=false&q=&cursor=&limit=` → `ProjectPage`：
`items`（`ProjectOut`：`id`、`integration_id`、`external_id`、`title`、`archived`、`source_url`、`source_updated_at`、`synced_at`）、
`next_cursor`、`total_estimate`、`sync`（第 10 节）。默认不含已归档项目；按标题排序。

## 6. 任务

### 6.1 TaskOut

| 字段 | 说明 |
|---|---|
| `id` | 投影 UUID |
| `integration_id`、`project_id` | `project_id` 是项目投影的 UUID；所属项目尚未同步时为 null |
| `external_id`、`identifier` | `vikunja:task:42`；Vikunja 的 `identifier`（如 `PROJ-12`） |
| `title` | |
| `description` | Markdown 文本（核验 ADR 第 4 节：以 `format=markdown` 读写）；**前端按纯文本显示**，不渲染 HTML |
| `done`、`done_at` | |
| `priority` | 0 未设置、1 低、2 中、3 高、4 紧急、5 立即 |
| `due_at` | 带时区；Vikunja 的零值日期映射为 null |
| `labels` | 标签标题列表；本 Sprint 只读 |
| `source_url` | 原生界面 `<public_url>/tasks/<id>` |
| `version` | 乐观并发用的不透明字符串（取自 Vikunja 的 `updated`）；写请求原样带回 |
| `source_updated_at`、`synced_at` | 外部最后修改时间；投影最后更新时间 |

### 6.2 列表

`GET /api/v1/tasks` 参数：`status`（`open` 默认 / `done` / `all`）、`project_id`、`priority`（可重复，0–5）、`due_before`、`due_after`（带时区）、
`q`（标题包含，1–100 字）、`sort`（`due_at` 默认，没有截止日期的排最后 / `priority` / `updated`）、`cursor`、`limit`（默认 30，最大 100）。
返回 `TaskPage`：`items`、`next_cursor`、`total_estimate`、`sync`。看板视图由前端按 `done` 分列，不需要额外接口。

### 6.3 写入

- `TaskCreate`：`project_id`（必填）、`title`（1–250）、`description`（≤ 10000）、`priority`（默认 0）、`due_at`（可为 null）。
- `TaskUpdate`：`version`（必填）与 `title`、`description`、`priority`、`due_at`（显式 null 表示清除）、`done`、`project_id`（移动到别的项目）中至少一项；
  只发送要改的字段（服务端用 merge-patch，不整体覆盖）。
- `DELETE /api/v1/tasks/{task_id}?version=…`。Vikunja 里已经不存在时也返回 204，并在投影里记 tombstone（幂等）。
- **并发**（核验 ADR 第 5.3 节：Vikunja 写入不校验 `If-Match`）：修改与删除前服务端先读取 Vikunja 上的当前任务，
  `updated` 与请求的 `version` 不一致时返回 `409 VERSION_CONFLICT`，`details` 带 `current_version` 与最新的 `current`（TaskOut）。
  读取与写入之间仍有很短的窗口，期间别人的修改会被覆盖——这是 Vikunja 侧“后写覆盖”的限制，契约明确接受。
- 本 Sprint 不支持写标签、负责人、评论与附件（需要额外权限和多次调用，核验 ADR 第 5.1 节）；需要时在原生界面操作。

## 7. Idempotency-Key（规范 4.1、6.3；验收 A06、A07）

- `POST /tasks`、`PATCH /tasks/{id}`、`DELETE /tasks/{id}` 必须带请求头 `Idempotency-Key`（UUID）；缺少或格式不对返回 `422 VALIDATION_FAILED`。
- 作用范围：工作空间 + 操作者 + 键；记录保留 24 小时。请求指纹 = 方法 + 路径 + 规范化后的 JSON 请求体（键排序）的 SHA-256。
- 前端：**每次用户提交生成一个新键**；网络重试、“查询结果”都复用同一个键；收到 `OPERATION_NOT_APPLIED` 后若用户确认重试，换新键。

| 情况 | 响应 |
|---|---|
| 第一次请求 | 正常执行；记录状态 `processing` |
| 同键、同请求，已成功 | 原样返回当时的状态码与响应体，响应头 `Idempotent-Replayed: true`（A06：最多创建一个外部任务） |
| 同键、同请求，已确定失败（外部 403 / 404 / 422 等） | 原样返回当时的错误 |
| 同键、同请求，仍在处理 | `409 IDEMPOTENCY_IN_PROGRESS`，`Retry-After: 1` |
| 同键、**不同**请求 | `409 IDEMPOTENCY_KEY_REUSED`（A07） |
| 请求已发出但超时 / 连接中断 | `504 RESULT_UNKNOWN`，`details.retry_with_same_key = true`；记录状态 `unknown` |
| 同键、同请求，状态 `unknown` | 服务端先查询结果：创建——在目标项目里查找请求开始之后、由 Token 所有者创建、标题相同的任务（核验 ADR 第 5.4 节）；修改 / 删除——读取当前任务判断是否已生效。找到 → 按成功返回（201 / 200 / 204）；确认没有生效 → `409 OPERATION_NOT_APPLIED`（需要换新键重试）；Vikunja 仍不可用 → `503 EXTERNAL_UNAVAILABLE`，状态保持 `unknown` |
| 确定没有发出（连接被拒、DNS 失败等） | `503 EXTERNAL_UNAVAILABLE`；不保存记录，可以用同一个键重试 |

## 8. 外部错误到 HTTP 的映射

工作台的 `401` 只表示“工作台自己的会话无效”（前端会跳登录页），所以 Vikunja 的鉴权失败用 `502`：

| Connector 分类（规范第 7 节） | HTTP | code | 说明 |
|---|---|---|---|
| `AUTH_FAILED`（Vikunja 401） | 502 | `INTEGRATION_UNAUTHORIZED` | 集成状态改为 `unauthorized` |
| `AUTH_FAILED`（Vikunja 403，资源级） | 403 | `EXTERNAL_FORBIDDEN` | 只是这个项目 / 任务无权访问 |
| 外部资源不存在（404） | 404 | `NOT_FOUND` | 同时在投影里记 tombstone |
| `VALIDATION_FAILED` | 422 | `EXTERNAL_VALIDATION_FAILED` | `details.errors` 只含位置与原因；去掉 Vikunja 回显的 `value` |
| `RATE_LIMITED` | 503 | `EXTERNAL_RATE_LIMITED` | 带 `Retry-After` |
| `UNAVAILABLE` | 503 | `EXTERNAL_UNAVAILABLE` | |
| `TIMEOUT_UNKNOWN`（写操作） | 504 | `RESULT_UNKNOWN` | 第 7 节 |
| 集成未配置、已停用或未测试通过 | 409 | `INTEGRATION_UNAVAILABLE` | |

## 9. Webhook 端点 `POST /api/v1/webhooks/vikunja/{integration_id}`

- 不用会话与 CSRF；只认签名：`X-Vikunja-Signature` = 用该集成的 Webhook 密钥对**原始请求体**做 HMAC-SHA256 的十六进制（核验 ADR 第 6 节），常量时间比较。
- 请求体上限 256 KiB（Vikunja 的单次投递约 3 KB），超过返回 `413 PAYLOAD_TOO_LARGE`；不是 JSON 返回 `400 BAD_REQUEST`。
- 集成不存在、已停用、缺少签名或签名不对，**一律** `401 WEBHOOK_SIGNATURE_INVALID`（不泄露集成是否存在）。
- 签名正确：以 `event_key = sha256(原始请求体)` 写入 Webhook Inbox（重复的忽略），立即返回 `202 {"status": "accepted"}`；处理在 Worker 里异步进行（T-0021）。
- **payload 不信任**（规范第 9 节）：Inbox 只保存事件名、时间与任务 / 项目的 ID，不保存 `doer` 等其余内容；Worker 处理时以 Vikunja API 读到的当前状态为准
  （删除事件也先确认 404），因此重复、乱序的事件不会写入旧数据。
- 注册：同步时如果 Token 有 `projects_webhooks` 权限，服务端为每个项目注册（或修复）一个 Webhook，事件为
  `task.created`、`task.updated`、`task.deleted`、`project.updated`、`project.deleted`，目标地址为
  `<WEBHOOK_BASE_URL>/api/v1/webhooks/vikunja/<integration_id>`（部署配置，Compose 中为 `http://caddy:8080`）。没有该权限时只靠周期对账。

## 10. 同步状态与首页任务模块

`SyncState`（集成、项目列表、任务列表都返回）：`status`（`ok` / `stale` / `unavailable` / `unauthorized` / `not_configured`）、
`as_of`（最近一次成功同步的时间，从未成功为 null）、`reason`（机器可读）。规则：

| 条件 | status |
|---|---|
| 工作空间没有 Vikunja 集成，或集成已停用 | `not_configured` |
| 集成 `unauthorized` | `unauthorized` |
| 从未成功同步 | `unavailable`（`reason`：`not_synced`） |
| 最近一次成功同步在 15 分钟内 | `ok` |
| 更早 | `stale` |

首页聚合（Sprint 2 契约 ADR 第 8 节）的 `modules.tasks` 改为按上表给出（`not_configured` 时仍是 `unavailable` + `reason: not_configured`，与 Sprint 2 一致）。
状态为 `ok` / `stale` 时：`summary.pending_tasks` = 未完成任务数，`summary.active_projects` = 未归档项目数，
`today_tasks` = 截止时间早于“明天 0 点（UTC）”的未完成任务（含已逾期），按截止时间排序，最多 20 条。其余状态下仍为 null / 空数组。
周期对账的频率与做法由 T-0021 决定（增量 + 定期全量，覆盖删除与丢失的事件）。

## 11. 权限

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看集成、项目、任务 | ✓ | ✓ | ✓ | ✓ |
| 创建 / 修改 / 删除任务 | ✓ | ✓ | ✓ | — |
| 新建 / 修改集成、测试连接、手动同步 | ✓ | ✓ | — | — |

## 12. 审计日志

沿用 Sprint 1 契约第 11 节：`integration.created`、`integration.updated`（metadata 记录改了哪些字段，Token 只记“已更换”）、`integration.tested`、
`integration.sync_requested`、`task.created`、`task.updated`（记录改了哪些字段）、`task.deleted`。metadata 只含 ID 与字段名，不含 Token、任务标题与描述。

## 13. 新增错误码

| HTTP | code | 场景 |
|---|---|---|
| 409 | `IDEMPOTENCY_KEY_REUSED` | 同一个幂等键用于不同的请求 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | 同一个请求仍在处理 |
| 409 | `OPERATION_NOT_APPLIED` | 结果未知的写操作经查询确认没有生效 |
| 409 | `INTEGRATION_UNAVAILABLE` | 集成未配置、已停用或尚未测试通过 |
| 409 | `SYNC_IN_PROGRESS` | 已有同步在运行 |
| 401 | `WEBHOOK_SIGNATURE_INVALID` | Webhook 签名缺失或不对 |
| 403 | `EXTERNAL_FORBIDDEN` | Vikunja 拒绝访问该资源 |
| 413 | `PAYLOAD_TOO_LARGE` | Webhook 请求体过大 |
| 422 | `EXTERNAL_VALIDATION_FAILED` | Vikunja 拒绝请求内容 |
| 502 | `INTEGRATION_UNAUTHORIZED` | Vikunja Token 无效、过期或权限不足 |
| 503 | `EXTERNAL_UNAVAILABLE` / `EXTERNAL_RATE_LIMITED` | Vikunja 不可用 / 限流 |
| 504 | `RESULT_UNKNOWN` | 写操作结果未知 |

沿用：`VERSION_CONFLICT`、`LIMIT_EXCEEDED`（Sprint 2）以及 Sprint 1 第 7 节的全部错误码。

## 14. 不在本契约内

标签 / 负责人 / 评论 / 附件的写入、看板分组（Vikunja 的 bucket）、重复任务、多个同类集成、实时推送（SSE）、跨资源搜索（Sprint 4）。

## 15. 后果

- T-0020 / T-0021 按本契约实现；T-0022 前端用重新生成的 `schema.d.ts`，按第 7 节生成与复用幂等键、按第 8 节处理错误码。
- 投影与幂等记录的表结构（`integrations`、`integration_accounts`、`external_resources`、`idempotency_requests`、`webhook_inbox`、`sync_cursors`）在 T-0020 / T-0021 的迁移里给出，写入数据字典。
- 以后升级 Vikunja 时，先按核验 ADR 重新核验，再看本契约是否需要变化。
