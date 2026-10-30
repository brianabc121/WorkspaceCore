# Sprint 4 接口契约：知识入口与收藏、本地日历、跨资源搜索、GitHub 只读、首页模块

- 状态：accepted
- 日期：2026-10-30
- 作者：claude（协作任务 T-0026）
- 依据：`docs/decisions/2026-10-30-github-rest-and-affine-verification.md`（下称“核验 ADR”）；
  规范第 4.2、5.1（第 10 ~ 13 项）、5.3、6、7.2、7.3、8.2 节；验收场景 A10 ~ A13、A17
- 实现：后端 T-0027（知识入口、日历）、T-0028（GitHub）、T-0029（搜索）；前端 T-0030（知识入口、日历）、T-0031（GitHub、全局搜索）

## 1. 原则

- **数据所有权**（规范 4.2）：
  - AFFiNE 文档：工作台只保存收藏的链接与手工元数据，不读正文，不向 AFFiNE 发任何请求（核验 ADR 第 8 节）。
  - 本地日历：工作台是主源。
  - GitHub：工作台保存只读的状态投影。
- 与 Sprint 3 一致：
  - 都是工作空间范围的接口（`X-Workspace-Id`），viewer 可读，member 及以上可写，集成与知识来源的配置要 admin；写接口要 CSRF；
  - 读外部数据一律读本地投影，带 `sync` 新鲜度；
  - 未实现的端点运行时返回 501，但不写进 OpenAPI。
- **只增不改**：Sprint 1 ~ 3 已有的请求 / 响应字段与枚举值都不变。新字段都有默认值，在生成的 TypeScript 类型里是可选的，
  前端现有代码与测试数据不受影响（D-0005）。
- **本地数据的写入不需要 `Idempotency-Key`**（收藏、知识来源、日历事件）。理由：
  - 它们在一个数据库事务里完成，不存在“外部结果未知”；
  - 与 Sprint 2 的仪表盘一致，由前端防重复提交；
  - 幂等键只用于写第三方系统（Sprint 3 契约第 7 节）。

## 2. 端点总览

| 方法 | 路径 | 角色 | 成功响应 | 实现 |
|---|---|---|---|---|
| GET | `/api/v1/knowledge/sources` | viewer | 200 `Page[KnowledgeSourceOut]` | T-0027 |
| POST | `/api/v1/knowledge/sources` | admin | 201 `KnowledgeSourceOut` | T-0027 |
| PATCH | `/api/v1/knowledge/sources/{source_id}` | admin | 200 `KnowledgeSourceOut` | T-0027 |
| DELETE | `/api/v1/knowledge/sources/{source_id}?version=` | admin | 204 | T-0027 |
| GET | `/api/v1/knowledge/resources` | viewer | 200 `Page[KnowledgeResourceOut]` | T-0027 |
| POST | `/api/v1/knowledge/bookmarks` | member | 201 `KnowledgeResourceOut` | T-0027 |
| PATCH | `/api/v1/knowledge/bookmarks/{bookmark_id}` | member | 200 `KnowledgeResourceOut` | T-0027 |
| DELETE | `/api/v1/knowledge/bookmarks/{bookmark_id}?version=` | member | 204 | T-0027 |
| GET | `/api/v1/calendar/events` | viewer | 200 `Page[CalendarEventOut]` | T-0027 |
| POST | `/api/v1/calendar/events` | member | 201 `CalendarEventOut` | T-0027 |
| PATCH | `/api/v1/calendar/events/{event_id}` | member | 200 `CalendarEventOut` | T-0027 |
| DELETE | `/api/v1/calendar/events/{event_id}?version=` | member | 204 | T-0027 |
| POST | `/api/v1/integrations`（provider `github`） | admin | 201 `IntegrationOut` | T-0028 |
| GET | `/api/v1/developer/repos` | viewer | 200 `RepoPage` | T-0028 |
| GET | `/api/v1/developer/issues` | viewer | 200 `IssuePage` | T-0028 |
| GET | `/api/v1/developer/pulls` | viewer | 200 `PullPage` | T-0028 |
| GET | `/api/v1/developer/runs` | viewer | 200 `RunPage` | T-0028 |
| GET | `/api/v1/search` | viewer | 200 `SearchPage` | T-0029 |

- 非成员 404、角色不够 403（沿用 Sprint 1）。
- 分页沿用规范 6.4：`limit` 默认 30、最大 100；`cursor` 是上一页的 `next_cursor`。
- 已有的 `/integrations/{id}`（PATCH）、`/test`、`/sync` 同样适用于 GitHub 集成（第 6 节）。

## 3. 知识来源（AFFiNE 外部地址）

核验 ADR 第 8.3 节第 1 条要求“工作台配置一个 AFFiNE 外部地址”。它是工作空间级的配置，由 admin 管理，单独成为一种资源。

- `KnowledgeSourceCreate`：`provider`（本 Sprint 只有 `affine`）、`name`（1–64）、`base_url`。
  - `base_url` 是 AFFiNE 的 `server.externalUrl`，可以带子路径。
  - 规则：只允许 `http` / `https`，不带用户名密码、查询串或片段；保存时去掉末尾的 `/`。
  - 拒绝链路本地地址（`169.254.0.0/16`、`fe80::/10`，含云元数据地址 `169.254.169.254`）与未指定地址（`0.0.0.0`、`::`）（A12）。
  - 允许回环与内网地址：本机开发时 AFFiNE 常在 `http://localhost:3010`，链接只在用户自己的浏览器里打开。
  - 工作台从不访问这个地址，所以**不做 SSRF 解析检查**，只检查字符串。
- `KnowledgeSourceOut`：`id`、`provider`、`name`、`base_url`、`tested_version`（`0.27.4`）、`bookmark_count`、`version`、`created_at`、`updated_at`。
- `KnowledgeSourceUpdate`：`version`（必填）与 `name`、`base_url` 中至少一项。
  修改 `base_url` 后，该来源下所有收藏的链接都随之改为新地址：收藏只保存路径部分（第 4.2 节），AFFiNE 换域名时不用逐条修改。
- `DELETE …/sources/{id}?version=`：连同该来源的全部收藏（含私有收藏）、关联和搜索索引一起删除。前端要二次确认，并说明会删除多少条收藏。
- V1 每个工作空间最多 1 个知识来源，第 2 个返回 `409 LIMIT_EXCEEDED`。
- 知识来源**不出现在** `GET /api/v1/integrations` 里：那里只有需要凭证、会同步的集成（Vikunja、GitHub）。存储方式由 T-0027 决定。

## 4. 收藏（AFFiNE 深链接）

### 4.1 KnowledgeResourceOut

| 字段 | 说明 |
|---|---|
| `id` | 收藏的 UUID |
| `source_id`、`provider` | 所属知识来源；`provider` 为 `affine` |
| `resource_type` | `affine_doc` |
| `title`、`note` | 用户填写的标题（1–200）与备注（≤ 2000，纯文本，前端不渲染 HTML / Markdown） |
| `url` | 规范化后的完整链接：来源的 `base_url` + 路径 + 保留的查询参数（第 4.2 节）。前端用它做“在 AFFiNE 中打开” |
| `link_kind` | `workspace_doc`（`/workspace/<ws>/<doc>`）或 `share`（`/share/<ws>/<doc>`） |
| `affine_workspace_id`、`doc_id` | 从链接解析出的 ID，只用于显示与去重 |
| `visibility` | `workspace`：工作空间成员都能看到；`private`：只有创建者能看到 |
| `owner_id` | 创建者；用户被删除后为 null |
| `links` | 关联的 Vikunja 项目 / 任务：`[{resource_id, resource_type: task \| project, title, source_url}]` |
| `version`、`created_at`、`updated_at` | `version` 用于乐观并发 |

### 4.2 链接校验（核验 ADR 第 8.3 节，T-0027 实现）

`BookmarkCreate.url` 在服务端按下列规则校验，**只看字符串，不发请求**：

1. 工作空间必须已配置知识来源，否则返回 `409 INTEGRATION_UNAVAILABLE`（`details.reason = not_configured`）。
2. scheme、主机、端口与来源的 `base_url` 完全一致，路径以来源的子路径开头；拒绝 `javascript:`、`data:`、`file:` 以及带用户名密码的 URL。
3. 子路径之后只能是 `/workspace/<ws>/<doc>` 或 `/share/<ws>/<doc>`：
   - `<ws>`、`<doc>` 只允许 `[A-Za-z0-9_-]`，长度 1 ~ 64；
   - `<doc>` 不能是 `all`、`collection`、`tag`、`trash`、`journals`、`settings`、`chat` 这些功能页。
4. 查询参数只保留 `mode`（`page` / `edgeless`）、`blockIds`、`elementIds`、`databaseId`、`databaseRowId`、`commentId`，其余丢弃；丢弃 `#` 片段。
5. URL 长度不超过 2048。

不合规时返回 `422 VALIDATION_FAILED`：`details.errors[0].loc = ["body", "url"]`，`details.reason` 为下表之一。错误消息不回显 URL。

| reason | 含义 |
|---|---|
| `scheme_not_allowed` | 不是 http(s)，或与来源的 scheme 不一致 |
| `credentials_not_allowed` | URL 里带用户名或密码 |
| `host_mismatch` | 主机、端口或子路径与知识来源不一致 |
| `not_a_document` | 路径不是文档链接（功能页、工作空间首页等） |
| `invalid_id` | 工作空间或文档 ID 的字符或长度不合规 |

### 4.3 写入与权限

- `BookmarkCreate`：`url`、`title`、`note`（默认空）、`visibility`（默认 `workspace`）、`linked_resource_ids`（Vikunja 项目 / 任务投影的 UUID，最多 20 个，默认空）。
- `BookmarkUpdate`：`version`（必填）与 `url`、`title`、`note`、`visibility`、`linked_resource_ids` 中至少一项。
  - 都不能为 null；`linked_resource_ids` 整体替换。
  - 版本不一致返回 `409 VERSION_CONFLICT`（`details.current_version`）。
- `DELETE …/bookmarks/{id}?version=` → 204。
- **去重**：同一个知识来源里，同一个 `(affine_workspace_id, doc_id)`：
  - 工作空间可见的收藏最多 1 条；
  - 每个用户自己的私有收藏最多 1 条。

  冲突返回 `409 BOOKMARK_EXISTS`，`details.existing_id` 是那条收藏的 ID。
  别人的私有收藏不参与冲突判断，也就不会因此暴露它的存在。
- **可见性**（S4-01“权限可见性”，A11）：
  - 私有收藏只有创建者能读、改、删、搜到。其他人（包括 owner / admin）访问时返回 404，与不存在一样。
  - 工作空间可见的收藏：成员都能看，member 及以上都能改标题、备注、关联、链接。
  - **只有创建者能修改 `visibility`**，其他人修改返回 `403 FORBIDDEN`；创建者已被删除的收藏保持工作空间可见。
- **关联**：`linked_resource_ids` 必须是本工作空间中未删除的 Vikunja 项目或任务，否则返回 `422 VALIDATION_FAILED`（`loc` 指向该字段）。
  - 被关联的项目 / 任务在 Vikunja 里删除（投影写 tombstone）后，它不再出现在任何收藏的 `links` 里，页面不留僵尸引用（A10）。
  - 投影行恢复（例如对账发现误判）时，关联重新出现。

### 4.4 列表 `GET /api/v1/knowledge/resources`

- 参数：
  - `source_id`；
  - `linked_resource_id`：只返回关联了某个项目 / 任务的收藏，任务抽屉的“相关文档”用它；
  - `visibility`：`workspace` / `private` / `all`（默认），`private` 只返回自己的；
  - `q`：标题或备注包含，1–100 字；
  - `sort`：`updated`（默认，最近修改在前）/ `title`；
  - `cursor`、`limit`。
- 返回 `Page[KnowledgeResourceOut]`。没有配置知识来源时返回空列表，前端提示去配置。

## 5. 本地日历

### 5.1 CalendarEventOut

| 字段 | 说明 |
|---|---|
| `id` | UUID |
| `title`、`description`、`location` | 标题 1–200；说明 ≤ 5000（纯文本）；地点 ≤ 200 |
| `all_day` | 是否全天事件 |
| `start_at`、`end_at` | 开始 / 结束的 UTC 时间（结束不含）。全天事件按 `timezone` 换算成当地 0 点 |
| `start_date`、`end_date` | 只有全天事件有值，其余为 null。日期，`end_date` 不含（1 天的事件 `end_date` = `start_date` + 1） |
| `timezone` | IANA 时区名，例如 `Asia/Shanghai`；前端按它显示时间 |
| `owner_id` | 创建者；用户被删除后为 null |
| `version`、`created_at`、`updated_at` | |

### 5.2 写入

- `CalendarEventCreate` 需要 `title`、`timezone`（必须是有效的 IANA 时区），以及下面两种时间之一：
  - 定时事件：`all_day = false`（默认），带 `start_at`、`end_at`，都要带时区偏移；
  - 全天事件：`all_day = true`，带 `start_date`、`end_date`。

  两种时间字段不能混用。结束必须晚于开始，持续时间不超过 92 天。
  时区名用 Python `zoneinfo` 校验，依赖系统的时区数据：API 镜像 `python:3.12.15-slim-trixie` 自带，实测 486 个时区。
- `CalendarEventUpdate`：`version`（必填）加要改的字段（merge-patch），都不能为 null。
  - 改 `all_day` 时必须同时给出对应的一对时间；
  - 服务端按合并后的结果再校验一次，不合规返回 `422`；
  - 版本不一致返回 `409 VERSION_CONFLICT`。
- `DELETE …/events/{id}?version=` → 204。
- 日历在工作空间内共享：member 及以上可以修改、删除任何事件（与任务一致）。V1 没有私有事件。
- **重复事件（`rrule`）V1 不实现**（规范 5.1 第 13 项允许）：请求里没有这个字段，多余字段返回 422。需要重复时逐个创建。

### 5.3 查询

- `GET /api/v1/calendar/events?start=&end=`：两个参数都必填，都要带时区偏移，`end` 晚于 `start`，跨度不超过 92 天，否则返回 422。
- 返回与区间有重叠的事件（`start_at < end` 且 `end_at > start`），按 `start_at`、`id` 排序，分页。
- agenda 由前端按 `timezone` 分组到天，不需要额外接口。

## 6. GitHub 集成（provider `github`）

### 6.1 新建

`POST /api/v1/integrations` 的请求体按 `provider` 区分（discriminated union）：
- `provider: vikunja` 仍是 Sprint 3 的 `IntegrationCreate`，不变；
- `provider: github` 是 `GitHubIntegrationCreate`，字段如下：

| 字段 | 说明 |
|---|---|
| `name` | 1–64 |
| `api_token` | 细粒度 Personal Access Token，必须以 `github_pat_` 开头；只写不读（核验 ADR 第 3 节）。经典 Token（`ghp_`）返回 422 并提示改用细粒度 Token |
| `token_expires_at` | 可选，与在 GitHub 创建 Token 时设置的过期时间一致 |
| `repositories` | 要同步的仓库 `owner/repo`，1–20 个，忽略大小写去重。owner `[A-Za-z0-9-]{1,39}`，仓库名 `[A-Za-z0-9._-]{1,100}`，且不能是 `.` 或 `..`（工作台自己的输入限制，以 GitHub 实际返回为准） |

- 不接受 `base_url` / `public_url`：V1 固定为 `https://api.github.com` 和 `https://github.com`（核验 ADR 第 1 节）。
- 同样每个工作空间最多 1 个 GitHub 集成。

### 6.2 IntegrationOut 与修改

- `IntegrationOut.provider` 增加 `github`；新增字段 `repositories`（GitHub 集成配置的仓库；Vikunja 集成为空数组）。
- GitHub 集成的固定值：

| 字段 | 值 |
|---|---|
| `base_url` | `https://api.github.com` |
| `public_url` | `https://github.com` |
| `tested_version` / `api_version` | `2026-03-10` |
| `instance_version` | null |
| `webhooks` | `{mode: unavailable, registered_projects: 0, reason: not_supported}`（核验 ADR 第 6 节） |

- `IntegrationUpdate` 新增 `repositories`（只用于 GitHub，整体替换）：
  - 修改仓库列表后不需要重新测试，下一次同步按新列表进行；被移出的仓库的投影写 tombstone。
  - `api_token` 接受 `tk_`（Vikunja）或 `github_pat_`（GitHub），**由服务端检查与集成的 provider 一致**，不一致返回 422。
  - 对 GitHub 集成修改 `base_url` / `public_url`，或对 Vikunja 集成修改 `repositories`，都返回 `422 VALIDATION_FAILED`。

### 6.3 测试连接

`POST /integrations/{id}/test` 的 `checks[]` 沿用已有的名称（取值不扩展，前端现有代码不受影响）：

| name | GitHub 上的含义 |
|---|---|
| `reachable` | 能访问 `https://api.github.com` |
| `version` | `X-GitHub-Api-Version: 2026-03-10` 被接受（没有 400 / 410） |
| `token` | Token 有效（没有 401） |
| `webhooks` | 总是 `ok: false`、`code: not_supported`；可选项，不影响 `ok` |

- 每个仓库的权限检查放在新增的可选字段 `repositories` 里：
  `[{full_name, ok, checks: [{name: metadata | issues | pull_requests | actions, ok, code}]}]`。
  - 方法：对每个仓库调一次核验 ADR 第 3 节表中的端点；
  - 缺少权限时 `code = missing_permission`，仓库不存在或不在 Token 的范围内时 `code = not_found`。
- `ok` = `reachable`、`version`、`token` 通过，而且所有仓库的四项检查都通过。
- 能识别出多余权限时（例如响应带非空的 `X-OAuth-Scopes`），在 `warnings` 里提示。
- Vikunja 集成的 `repositories` 为 null。

## 7. 开发者页（GitHub 投影）

四个列表都读本地投影，按集成过滤，带 `sync: SyncState`。没有 GitHub 集成时返回空列表，`sync.status = not_configured`。

| 端点 | 参数 | 条目 |
|---|---|---|
| `GET /developer/repos` | `integration_id`、`cursor`、`limit` | `RepoOut` |
| `GET /developer/issues` | `repo_id`、`state`（`open` 默认 / `closed` / `all`）、`q`（标题包含）、`cursor`、`limit` | `IssueOut` |
| `GET /developer/pulls` | `repo_id`、`state`（同上）、`q`、`cursor`、`limit` | `PullOut` |
| `GET /developer/runs` | `repo_id`、`status`（`queued` / `in_progress` / `completed` 等，见核验 ADR 第 4 节）、`conclusion`（`failure` 等）、`cursor`、`limit` | `RunOut` |

- 共同字段：
  - `id`：投影 UUID；
  - `integration_id`；
  - `external_id`：`github:<类型>:<GitHub 数字 id>`；
  - `source_url`：GitHub 的 `html_url`；
  - `source_updated_at`、`synced_at`。

  Issue、PR、运行还有 `repo_id`（仓库投影的 UUID）与 `repo_full_name`。
- `RepoOut`：`full_name`、`description`、`private`、`archived`、`default_branch`、`pushed_at`、
  `access`（`ok` / `missing_permission` / `not_found`，来自最近一次同步）。
- `IssueOut`：
  - `number`、`title`、`state`（`open` / `closed`）、`state_reason`；
  - `author`（登录名）、`labels`（名称）、`assignees`（登录名）、`comments`；
  - `created_at`、`closed_at`。

  Issue 列表**不含 PR**（核验 ADR 第 4 节）。
- `PullOut`：
  - `number`、`title`、`state`、`draft`、`merged`、`merged_at`；
  - `author`、`labels`、`requested_reviewers`（登录名）；
  - `head_ref`、`base_ref`、`created_at`、`closed_at`。
- `RunOut`：
  - `name`、`display_title`、`status`、`conclusion`、`event`；
  - `head_branch`、`head_sha`、`run_number`、`run_attempt`、`actor`、`run_started_at`；
  - `failure_summary`：失败的运行才有，其余为 null。结构是 `{failed_jobs: [{name, failed_steps: [step 名称]}]}`。
    只来自 jobs 接口的名称与结论，**不读日志**。
- 排序：Issue / PR 按 GitHub 的 `updated_at` 倒序，运行按 `run_started_at` 倒序，仓库按 `full_name`。
- **SyncState 新增可选字段 `retry_at`**（与 `ModuleStatus.retry_at` 相同）：表示被 GitHub 限流、预计恢复的时间（A17）。

| 条件 | sync.status | reason |
|---|---|---|
| 没有 GitHub 集成或已停用 | `not_configured` | `not_configured` |
| Token 被拒（401） | `unauthorized` | `token_rejected`（A13：不再自动重试，提示重新连接） |
| 从未成功同步 | `unavailable` | `not_synced`；限流导致时为 `rate_limited`，带 `retry_at` |
| 正在被限流 | `stale` | `rate_limited`，带 `retry_at`；数据照常显示 |
| 最近一次访问 GitHub 失败 | `stale` | `source_unavailable` |
| 最近一次成功同步在 `SYNC_STALE_AFTER_SECONDS` 内 | `ok` | |
| 更早 | `stale` | `sync_overdue` |

前端在 `rate_limited` 时显示“GitHub 限流中，预计 HH:MM 恢复”，**绝不把空列表当成“没有数据”**。同步频率与退避由 T-0028 按核验 ADR 第 5 节实现。

## 8. 跨资源搜索 `GET /api/v1/search`

- 参数：
  - `q`：必填，去掉首尾空白后 1–100 字；
  - `types`：可重复，取值 `task`、`project`、`bookmark`、`event`、`repo`、`issue`、`pull`，默认全部；
  - `cursor`、`limit`。
- 返回 `SearchPage`（`items`、`next_cursor`、`total_estimate` 恒为 null）。每个 `SearchResultOut`：

| 字段 | 说明 |
|---|---|
| `resource_type` | 上面七种之一 |
| `id` | 该资源在自己接口里的 UUID（任务、收藏、事件、Issue 等） |
| `title` | 标题；仓库为 `full_name` |
| `context` | 辅助信息：任务的项目名、Issue / PR 的仓库名、事件的日期等；可为 null |
| `provider` | `vikunja` / `github` / `affine` / `local` |
| `source_url` | 外部原生界面的链接；本地事件为 null |
| `updated_at` | 资源最后修改时间 |
| `match` | `title`（标题命中）/ `text`（只在备注、说明等正文命中） |

- **只返回当前用户在当前工作空间可见、且未删除的资源**：
  - 每次查询都带 workspace 过滤；
  - 私有收藏只对创建者可见（第 4.3 节）；
  - tombstone 与已删除的资源立即不再返回（A10、A11）。
- **索引的内容**（T-0029，规范 5.3：只索引授权可得的文本，绝不写入 Token、私钥与外部文档正文）：

| 资源 | 索引的字段 |
|---|---|
| 任务 | 标题、描述 |
| 项目 | 标题 |
| 收藏 | 标题、备注（不含 AFFiNE 文档正文） |
| 事件 | 标题、说明、地点 |
| 仓库 | `full_name`、描述 |
| Issue / PR | 标题、标签名 |

  Actions 运行不进索引。
- **相关性**（规范 5.3）：
  - 英文与技术词用 `tsvector`；
  - 中文用 `ILIKE` 加 `pg_trgm` 的相似度：能找到包含查询字符串的结果，但**不做分词，排序只按字面相似度**，相关性有限，在界面和文档里如实说明；
  - 排序：标题完全匹配 > 标题前缀 > 标题包含 > 正文包含，同一档按 `updated_at` 倒序，再按 `id`，保证分页稳定。

## 9. 首页模块与组件

### 9.1 modules

`DashboardOverview.modules` 增加三个键，规则沿用 Sprint 2 / 3（单个模块失败只影响对应组件）：

| 键 | 规则 |
|---|---|
| `calendar` | 本地数据，正常时 `ok`；读取出错时 `unavailable`（`reason: error`） |
| `knowledge` | 没有知识来源时 `unavailable` + `reason: not_configured`；否则 `ok` |
| `github` | 按第 7 节 `SyncState` 的规则映射，`not_configured` 与 Sprint 3 一样表示为 `unavailable` + `reason: not_configured`；限流时带 `retry_at` |

`ModuleStatus` 新增可选字段 `retry_at`。`summary` 不变。

### 9.2 新的组件类型（规范 8.2）

| widget_type | config | 数据来源 |
|---|---|---|
| `calendar_agenda` | `days`（1–14，默认 7）、`limit`（1–20，默认 5） | `GET /calendar/events?start=现在&end=现在+days` |
| `recent_docs` | `limit`（1–20，默认 5） | `GET /knowledge/resources?sort=updated&limit=` |
| `github_activity` | `kind`（`runs` 默认 / `pulls` / `issues`）、`limit`（1–20，默认 5） | 对应的 `/developer/*` 列表 |

组件自己调用列表接口，首页聚合只提供模块状态，不同步等待这些数据（规范 6.2）。

## 10. 权限

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看知识来源、工作空间可见的收藏、日历、开发者页、搜索 | ✓ | ✓ | ✓ | ✓ |
| 新建 / 修改 / 删除收藏，新建 / 修改 / 删除日历事件 | ✓ | ✓ | ✓ | — |
| 新建 / 修改 / 删除知识来源；新建 / 修改 GitHub 集成、测试连接、手动同步 | ✓ | ✓ | — | — |
| 私有收藏 | 只有创建者 | | | |

## 11. 审计日志

沿用 Sprint 1 契约第 11 节。metadata 只含 ID 与字段名，**不含**标题、备注、说明、URL、Token。新增事件：
- `knowledge_source.created` / `updated` / `deleted`（删除时记录删除了多少条收藏）；
- `bookmark.created` / `updated` / `deleted`；
- `calendar_event.created` / `updated` / `deleted`；
- GitHub 集成沿用 `integration.*`。

## 12. 新增错误码

| HTTP | code | 场景 |
|---|---|---|
| 409 | `BOOKMARK_EXISTS` | 同一文档已有同样可见性的收藏（`details.existing_id`） |

沿用：
- `409 INTEGRATION_UNAVAILABLE`（没有配置知识来源）、`409 VERSION_CONFLICT`、`409 LIMIT_EXCEEDED`；
- `422 VALIDATION_FAILED`（收藏链接的 `details.reason` 见第 4.2 节）；
- `403 FORBIDDEN`（修改别人收藏的可见性）；
- Sprint 3 的全部错误码。

## 13. 不在本契约内

- AFFiNE 的任何 API 调用、正文索引与原生编辑；
- 重复日历事件、日历订阅与导出（iCal / CalDAV）、私有日历事件；
- GitHub Webhook、GitHub 的写操作、GitHub Enterprise Server；
- Actions 日志；
- 搜索的中文分词（PGroonga 等）；
- 多个知识来源或多个同类集成；
- 实时推送（SSE，Sprint 5）。

## 14. 后果

- 后端：
  - T-0027 实现第 3 ~ 5 节与首页的 `calendar` / `knowledge` 模块；
  - T-0028 实现第 6、7 节与 `github` 模块；
  - T-0029 实现第 8 节，并在 T-0027 / T-0028 的写入路径上维护索引。
  - 迁移：`calendar_events`、`resource_links`、`search_documents`，以及收藏与知识来源的存储，由各自任务给出。
- 前端：
  - T-0030 / T-0031 用重新生成的 `schema.d.ts`；
  - 集成页的检查项标签可以保持不变，GitHub 的逐仓库检查读 `IntegrationTestResult.repositories`。
- 本 PR 在服务端增加两条检查：`api_token` 前缀与集成的 provider 一致；Vikunja 集成不能设置 `repositories`。
  其余新端点在实现之前返回 501。
