# 数据字典

> 状态：2026-11-07，迁移到 `0047`（T-0149）。目标实体清单见 `docs/DEVELOPMENT_SPEC.md` 第 5 节（V2 见 `docs/v2/TRANSFORMATION_PLAN.md` 第 5 节）；本文记录**已经迁移**的数据库对象。
> 迁移 `0001` ~ `0019` 都只新增对象，都有 downgrade；`0011` ~ `0019` 没有改动 `0010` 及以前的表。加列只有两处，都可空、无默认值：`0014` 给 `0013` 新建的 `im_messages` 加 `idempotency_key`，`0018` 给 `0011` 新建的 `agent_instances` 加 `im_token_expires_at`。`0020`（T-0082，移除 Vikunja）是例外：新建 `projects` / `tasks` 并转换 Vikunja 的投影，删除 `webhook_inbox` 表、`integrations.webhook_secret_ref` 列与 Vikunja 的数据，downgrade 只恢复结构、不恢复数据（见「迁移 `0020`」）。`0047`（T-0149，移除 n8n）删除退役的工作流与登录账号表及相应的触发幂等记录，downgrade 也只恢复空表（见「迁移 `0047`」）。升级与回退的做法见 `docs/deployment.md` 第 5 节。

## 约定

- 所有自有表放在 `workspace` schema；主键 UUID；时间字段 `TIMESTAMPTZ`（UTC）。
- 所有结构变化都通过 Alembic 迁移（`services/api/migrations/versions/`），并配有测试。
- 约束命名规则见 `services/api/app/models/base.py` 的 `NAMING_CONVENTION`。

## 已迁移对象

| 迁移 | 对象 | 说明 |
|---|---|---|
| `0001` | schema `workspace` | 工作台自有表的命名空间 |
| `0001` | 扩展 `pgcrypto` | `gen_random_uuid()` 等函数 |
| `0001` | 扩展 `pg_trgm` | 中文标题的 `ILIKE` / 三元组模糊搜索（规范第 5.3 节） |

两个扩展在 PostgreSQL 13+ 都是 trusted 扩展，数据库 owner 无需超级用户即可创建。
Alembic 版本表 `alembic_version` 位于 `public` schema。

## 表

### 迁移 `0002`（S1-01）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `users` | `id`、`email`、`password_hash`、`display_name`、`status`、`created_at`、`updated_at` | `email` 唯一且必须小写；`status` ∈ `active` / `disabled`；密码为 Argon2id 哈希 |
| `auth_sessions` | `id`、`user_id`、`token_hash`、`expires_at`、`last_seen_at`、`revoked_at`、`created_at` | 只存会话 Token 的 SHA-256；`token_hash` 唯一；删除用户时级联删除 |
| `external_identities` | `id`、`user_id`、`issuer`、`subject`、`created_at` | `(issuer, subject)` 唯一；为将来的 OIDC 预留 |
| `workspaces` | `id`、`name`、`owner_id`、`created_at`、`updated_at` | `owner_id` → `users`（RESTRICT：工作空间存在时不能删除其 owner） |
| `workspace_members` | `workspace_id`、`user_id`、`role`、`created_at` | 联合主键；`role` ∈ `owner` / `admin` / `member` / `viewer`；随工作空间或用户级联删除 |

### 迁移 `0003`（S1-05）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `audit_logs` | `id`、`workspace_id`、`actor_id`、`action`、`resource_type`、`resource_id`、`metadata_redacted_json`、`ip_hash`、`created_at` | 只追加：触发器 `audit_logs_append_only` 拒绝 UPDATE；`workspace_id` → `workspaces`（级联删除），为 NULL 表示与任何工作空间无关；`actor_id` 不加外键，用户删除后记录保留；`metadata_redacted_json` 写入前脱敏；`ip_hash` 为带密钥的哈希；索引 `(workspace_id, created_at, id)` 用于倒序分页 |

写入规则与事件清单见 Sprint 1 契约 ADR 第 11 节。

### 迁移 `0004`（S2-01）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `dashboards` | `id`、`workspace_id`、`owner_id`、`title`、`is_default`、`version`、`created_at`、`updated_at` | 随工作空间级联删除；`owner_id` → `users`（用户删除后置空）；部分唯一索引 `uq_dashboards_default_per_workspace` 保证每个工作空间恰好一个默认仪表盘；`version` 用于乐观并发 |
| `widgets` | `id`、`dashboard_id`、`widget_type`、`config_json`、`layout_json`、`version`、`created_at`、`updated_at` | 随仪表盘级联删除；`widget_type` 与 `config_json` 写入前按 Registry 校验；`layout_json` 为 `{desktop: {x,y,w,h}, mobile: {y,h}}` |

迁移 0004 为已有的工作空间补建默认“首页”与 4 个入门组件（只新增行，可回退）。规则见 Sprint 2 契约 ADR 第 3–6 节。

### 迁移 `0005`（S3-02 ~ S3-04）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `integrations` | `id`、`workspace_id`、`provider`、`name`、`base_url`、`public_url`、`status`、`settings_json`、`webhook_secret_ref`、`version`、`created_at`、`updated_at` | 随工作空间级联删除；`(workspace_id, provider, name)` 唯一，V1 每个工作空间每个 provider 最多 1 个由服务层保证；`status` ∈ `pending` / `active` / `unavailable` / `unauthorized` / `disabled`；`settings_json` 只放非敏感元数据（`tested_version`、`instance_version`、`last_tested_at`、Webhook 注册状态）；`webhook_secret_ref` 是加密后的签名密钥引用（`0020` 删除了这一列）；`version` 用于乐观并发 |
| `integration_accounts` | `id`、`integration_id`、`user_id`、`auth_type`、`credential_ref`、`scopes_json`、`expires_at`、`status`、`created_at`、`updated_at` | `integration_id` 唯一（V1 一个集成一个凭证），随集成级联删除；`user_id` → `users`（删除后置空）；`credential_ref` 只存 `enc:v1:` 加密引用（AES-256-GCM，附加数据绑定本行 ID），明文 Token 从不入库 |
| `external_resources` | `id`、`workspace_id`、`integration_id`、`resource_type`、`external_id`、`title`、`source_url`、`metadata_json`、`source_updated_at`、`synced_at`、`deleted_at` | 外部项目与任务的本地投影（只读缓存，Vikunja 是主源）；`(integration_id, resource_type, external_id)` 唯一；外部的更新时间比已存的旧时不覆盖（乱序事件）；外部删除后记 `deleted_at`（tombstone），查询一律排除；部分索引 `ix_external_resources_workspace_type`（`deleted_at IS NULL`）；任务的展示字段（完成、优先级、截止时间、标签、version 等）在 `metadata_json` 里。`0020` 起只有 GitHub 的投影：Vikunja 的项目与任务转进 `projects` / `tasks` 后随集成删除 |
| `idempotency_requests` | `id`、`workspace_id`、`actor_id`、`idempotency_key`、`request_fingerprint`、`status`、`response_code`、`response_json`、`context_json`、`created_at`、`updated_at`、`expires_at` | `(actor_id, idempotency_key)` 唯一；随工作空间或用户级联删除；`status` ∈ `processing` / `succeeded` / `failed` / `unknown`；`response_json` 保存重放用的响应（任务 DTO 或错误码，不含 Token 与外部响应原文）；保留 24 小时，`expires_at` 有索引供清理 |
| `sync_cursors` | `id`、`integration_id`、`resource_type`、`cursor_value`、`last_success_at`、`last_error`、`updated_at` | `(integration_id, resource_type)` 唯一，随集成级联删除；`last_success_at` 决定 `sync.status`（超过 `SYNC_STALE_AFTER_SECONDS` 为 stale）；`last_error` 只存错误分类，不存外部响应 |

规则见 Sprint 3 契约 ADR 第 4、7、10 节；全部是新表，回退即删除。

### 迁移 `0006`（S3-05）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `webhook_inbox` | `id`、`integration_id`、`event_key`、`event_type`、`payload_json`、`status`、`attempts`、`next_retry_at`、`received_at`、`processed_at`、`error_text` | 签名校验通过后才写入；`(integration_id, event_key)` 唯一，`event_key = sha256(原始请求体)`，重复送达只记录一次；随集成级联删除；`status` ∈ `pending` / `processing` / `completed` / `retry` / `dead_letter`；`payload_json` 只有事件名、时间与任务 / 项目 ID（不保存 `doer` 等内容）；`next_retry_at` 是下次可领取的时间，处理中时是租约到期时间；部分索引 `ix_webhook_pending`（`status IN ('pending','retry','processing')`）；`error_text` 只存错误分类；处理完超过 7 天的事件由 Worker 清理，`dead_letter` 保留。**`0020` 已删除这张表**（T-0082） |

`sync_cursors` 在 0005 已建立；T-0021 增加 `resource_type = full_sync` 一行，记录最近一次全量同步的时间。规则见 Sprint 3 契约 ADR 第 9、10.1 节。

### 迁移 `0007`（S4-01、S4-02）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `knowledge_sources` | `id`、`workspace_id`、`provider`、`name`、`base_url`、`version`、`created_at`、`updated_at` | 工作空间配置的 AFFiNE 外部地址（`provider = affine`），工作台从不访问它；随工作空间级联删除；V1 每个工作空间最多 1 个，由服务层加锁计数保证；`version` 用于乐观并发 |
| `bookmarks` | `id`、`workspace_id`、`source_id`、`title`、`note`、`link_kind`、`affine_workspace_id`、`doc_id`、`suffix`、`visibility`、`owner_id`、`version`、`created_at`、`updated_at` | AFFiNE 文档收藏，**不含文档正文**；随知识来源或工作空间级联删除；`suffix` 是来源地址之后的路径与保留的查询参数，完整链接 = 来源的 `base_url` + `suffix`；`visibility` ∈ `workspace` / `private`，`link_kind` ∈ `workspace_doc` / `share`（CHECK 约束）；`owner_id` → `users`（删除后置空，私有收藏随之对所有人不可见）；部分唯一索引 `uq_bookmarks_workspace_doc`（工作空间可见的每篇文档一条）与 `uq_bookmarks_private_doc`（每个用户的私有收藏每篇文档一条）；索引 `(workspace_id, updated_at)` |
| `resource_links` | `id`、`workspace_id`、`source_resource_id`、`target_resource_id`、`relation`、`created_at` | 跨系统关联（规范 5.1 第 11 项）：`source_resource_id` → `bookmarks`、`target_resource_id` → `external_resources`（Vikunja 项目 / 任务投影），任一方删除时级联删除；目标写了 tombstone（`deleted_at`）时读取一律排除，页面不留僵尸引用（A10）；`0020` 起目标改为 `projects` / `tasks` 的 ID（见「迁移 `0020`」）；`(source_resource_id, target_resource_id)` 唯一；`relation` 目前只有 `related` |
| `calendar_events` | `id`、`workspace_id`、`owner_id`、`title`、`description`、`location`、`all_day`、`start_at`、`end_at`、`start_date`、`end_date`、`timezone`、`rrule`、`version`、`created_at`、`updated_at` | 本地日历（工作台是主源）；随工作空间级联删除；`owner_id` → `users`（删除后置空）；`start_at` / `end_at` 总是 UTC，全天事件为 `timezone` 当地 0 点（结束不含），区间查询只比较这两列；CHECK：`end_at > start_at`，且全天事件必须有 `start_date` / `end_date`、定时事件必须没有；`rrule` V1 不使用（总是 NULL）；索引 `(workspace_id, start_at)` |

### GitHub 投影（T-0028，无新迁移）

GitHub 集成复用 `integrations`、`integration_accounts`、`external_resources`、`sync_cursors`，没有新表：

- `integrations.settings_json`（`provider = github`）：
  - `repositories`：要同步的仓库；
  - `etags`：各列表第一页的 ETag；
  - `repo_access`：各仓库最近一次的访问情况；
  - `rate_limited_until`：被限流时的恢复时间；
  - `last_tested_at`。

  都不含 Token。
- `external_resources.resource_type` ∈ `repo` / `issue` / `pull` / `run`：
  - `external_id` 是 GitHub 的数字 `id`；
  - Issue、PR、运行的 `metadata_json` 里有 `repo_resource_id`（所属仓库的投影 UUID）与 `repo_full_name`；
  - 运行的 `failure_summary` 只有失败的 job / step 名称，不含日志。
- `sync_cursors.resource_type = github`：增量同步的游标（上次同步开始时间往前 2 分钟）；全量同步的时间记在 `full_sync`。

### 迁移 `0008`（S4-03、S4-05）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `search_documents` | `id`、`workspace_id`、`resource_type`、`resource_id`、`provider`、`title`、`body`、`context`、`source_url`、`owner_id`、`visibility_json`、`source_updated_at`、`search_vector`、`indexed_at` | 跨资源搜索索引（规范 5.1 第 12 项），**只由触发器维护**，应用只读。触发器 `search_index` 挂在 `external_resources`、`bookmarks`、`calendar_events` 上，插入、更新、tombstone、删除都在同一个事务里同步；Actions 运行不进索引。只存标题、备注、说明、地点、标签与上下文（项目名 / 仓库名 / 日期），不含 Token 与外部文档正文。`(resource_type, resource_id)` 唯一；随工作空间级联删除；`owner_id` 只在私有收藏上有值（查询时只对该用户可见），创建者被删除的私有收藏从索引移除；`search_vector` 是 `to_tsvector('english', title \|\| ' ' \|\| body)` 的生成列（GIN 索引）；`title` 另有 `gin_trgm_ops` 索引供中文模糊匹配；收藏的 `source_url` 为 NULL，查询时用知识来源的地址拼出。`workspace.search_rebuild()` 整体重建（`python -m app.commands.reindex_search`） |

### 迁移 `0009`（S5-03）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `notifications` | `id`、`workspace_id`、`user_id`、`type`、`severity`、`title`、`body`、`link`、`source_key`、`read_at`、`created_at` | 通知中心，每个收件人一行。`(user_id, workspace_id, source_key)` 在 `source_key` 非空时唯一，同一事件不会重复通知；`ix_notifications_unread` 是只含未读的部分索引（未读数）；`ix_notifications_recipient_created` 用于按时间分页；随用户、工作空间级联删除。`title` / `body` 按纯文本显示，不含 Token 与第三方 payload |

### 迁移 `0010`（S5-04，历史结构；T-0149 已移除）

以下三表只用于解释历史迁移；迁移 `0047` 后不保留连接、白名单或执行记录，见 `docs/decisions/2026-11-07-remove-n8n.md`。

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `automation_connections` | `id`、`workspace_id`、`base_url`、`public_url`、`status`、`api_key_ref`、`trigger_secret_ref`、`settings_json`、`version`、`created_at`、`updated_at` | 每个工作空间最多一个 n8n 连接（`workspace_id` 唯一）；API Key 与触发共享密钥只存 `enc:v1` 引用，从不返回；随工作空间级联删除 |
| `automations` | `id`、`workspace_id`、`name`、`description`、`workflow_id`、`webhook_path`、`params_json`、`enabled`、`workflow_active`、`deleted_at`、`version`、`created_at`、`updated_at` | 白名单工作流，软删除（`deleted_at`）；未删除的行里 `(workspace_id, webhook_path)`、`(workspace_id, workflow_id)` 各自唯一（部分唯一索引）；`params_json` 是允许的参数定义，不含参数值 |
| `automation_runs` | `id`、`workspace_id`、`automation_id`、`status`、`trace_id`、`external_execution_id`、`triggered_by`、`error_summary`、`started_at`、`finished_at`、`created_at`、`updated_at` | 执行记录，保留（白名单删除后仍在）；**不保存触发参数与第三方 payload**；`(automation_id, external_execution_id)` 在后者非空时唯一；`ix_automation_runs_open` 是 `queued` / `running` / `unknown` 的部分索引，供对账；`triggered_by` 在用户删除时置空 |

### 迁移 `0011`（S6-01，T-0054）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_settings` | `workspace_id`、`agents_enabled`、`version`、`updated_at`、`updated_by` | 每个工作空间的 OpenClaw 实例总开关，`workspace_id` 即主键，随工作空间级联删除；没有这一行时视为打开；关闭后本工作空间所有实例认证立即失败，并尽力强制 IM 下线；`updated_by` → `users`（删除后置空）；`version` 用于乐观并发 |
| `agent_instances` | `id`、`workspace_id`、`name`、`description`、`network_mode`、`status`、`scope`、`max_role`、`im_user_id`、`im_nickname`、`im_ready`、`acting_user_id`、`reported_version`、`plugin_version`、`last_seen_at`、`bundle_issued_at`、`disabled_at`、`version`、`created_at`、`updated_at` | 实例登记；随工作空间级联删除，接口只能停用（`disabled_at`），不能删除；CHECK：`network_mode` ∈ `intranet` / `public`，`status` ∈ `pending` / `online` / `offline` / `disabled`，`scope` ∈ `read` / `read_write`（MCP 权限范围），`max_role` ∈ `admin` / `member` / `viewer`；`im_user_id`（`oc_` + 实例 UUID 十六进制）唯一；`(workspace_id, id)` 唯一，供凭证与密钥授权的复合外键保证同一工作空间；`acting_user_id` 是登记人 → `users`（删除后置空），实例以“登记人角色与 `max_role` 中较低的一个”调用工具；`im_ready` 表示 IM 账号已注册并与 owner / admin 互加好友，失败时由 Worker 每 5 分钟重试；`reported_version`、`plugin_version` 由心跳上报，`last_seen_at` 随心跳与认证更新；`version` 用于乐观并发 |
| `agent_credentials` | `id`、`workspace_id`、`instance_id`、`kind`、`ingress`、`token_hash`、`token_prefix`、`cert_fingerprint`、`expires_at`、`revoked_at`、`last_used_at`、`last_used_ip`、`created_at` | `(workspace_id, instance_id)` → `agent_instances`（复合外键，级联删除）；`kind` ∈ `token` / `client_cert`；`ingress` ∈ `intranet` / `public`，凭证绑定登记时的入口；CHECK `material`：令牌行只有 `token_hash`，证书行只有 `cert_fingerprint`。令牌只存 SHA-256（`token_hash` 唯一），`token_prefix` 是前 12 个字符，供界面辨认；客户端证书只存 SHA-256 指纹（小写十六进制，`cert_fingerprint` 唯一），证书与私钥只放进接入包、不入库，吊销以指纹为准。令牌没有到期时间，证书 `expires_at` 为 90 天后；`revoked_at` 在将来表示处于轮换宽限期（旧凭证再保留 24 小时）；同一种凭证同时最多两份有效，由服务层保证；部分索引 `ix_agent_credentials_active`（`revoked_at IS NULL`）；`last_used_at` / `last_used_ip` 至多每 60 秒更新一次，IP 原样保存 |
| `agent_secrets` | `id`、`workspace_id`、`name`、`kind`、`description`、`current_version`、`last_rotated_at`、`version`、`created_at`、`updated_at` | 密钥库条目，只有元数据，不含密钥值；随工作空间级联删除；`(workspace_id, name)` 唯一（重名返回 409 `AGENT_SECRET_EXISTS`）；`(workspace_id, id)` 唯一，供复合外键使用；`kind` ∈ `model_api_key` / `api_token` / `generic`；`current_version` 是当前值的版本号；删除密钥时它的版本与授权级联删除；`version` 用于乐观并发 |
| `agent_secret_versions` | `id`、`workspace_id`、`secret_id`、`value_version`、`ciphertext_ref`、`created_at`、`destroyed_at` | `(workspace_id, secret_id)` → `agent_secrets`（复合外键，级联删除）；`(secret_id, value_version)` 唯一；`ciphertext_ref` 是 `enc:v1:<key_id>:…` 引用，加密用途绑定 `agent_secret:<secret_id>:<value_version>`；只有当前版本有密文：轮换时旧版本的 `ciphertext_ref` 置空并记 `destroyed_at`，旧值不再下发、也无法恢复；管理员接口从不返回明文 |
| `agent_secret_grants` | `id`、`workspace_id`、`instance_id`、`secret_id`、`ref_id`、`created_at` | 实例可以取用的密钥；`ref_id` 是 OpenClaw 里用的 ID（如 `openai_api_key`），`(instance_id, ref_id)` 唯一；对实例、对密钥各有一个 `(workspace_id, …)` 复合外键，保证三者在同一工作空间，任一方删除时级联删除；授权按实例整体替换 |
| `agent_secret_accesses` | `id`、`workspace_id`、`instance_id`、`instance_name`、`ref_id`、`secret_id`、`value_version`、`result`、`source_ip`、`at` | 取密钥记录，每次请求的每个 `ref_id` 一行，**不含密钥内容**；单独成表，不进 `audit_logs`；`result` ∈ `ok` / `not_found` / `rate_limited`（未授权与不存在都记 `not_found`）；随工作空间级联删除；`instance_id`、`secret_id` 在对应记录删除后置空，`instance_name` 留作说明；`source_ip` 原样保存；索引 `(workspace_id, at)`、`(instance_id, at)`；目前没有清理任务 |
| `agent_ca` | `id`、`cert_pem`、`key_ref`、`not_after`、`created_at`、`retired_at` | 内部 CA，整个部署共用（不属于任何工作空间），给外网模式的实例签发客户端证书；表达式部分唯一索引 `uq_agent_ca_active`（`retired_at IS NULL`），同时只有一套在用；EC P-256，有效期 5 年，第一次需要时生成；`cert_pem` 只有证书；私钥只以 `enc:v1` 引用存在 `key_ref`（用途绑定 `agent_ca:<id>`）；不发布 CRL |

方案第 5 节要求给已有凭证密文加 `key_version` 列，实际没有加：`enc:v1:<key_id>:…` 引用本身带着加密密钥的 ID。`python -m app.commands.rotate_credentials` 按它找出用旧密钥加密的记录，逐条重新加密，不改 `updated_at` 与 `version`。覆盖的列：
- `integration_accounts.credential_ref`；
- `integrations.webhook_secret_ref`（`0020` 删除了这一列）；
- `agent_secret_versions.ciphertext_ref`；
- `agent_ca.key_ref`。

步骤见 `docs/deployment.md` 第 6.1 节。

### 迁移 `0012`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_accounts` | `user_id`、`im_user_id`、`created_at` | 管理员（owner / admin）的 OpenIM 账号，一个用户一个，多个工作空间共用，第一次需要时创建；`user_id` 即主键，随用户级联删除；`im_user_id`（`ws_` + 用户 UUID 十六进制）唯一；不保存 IM 令牌（imAdmin 令牌缓存在 Redis）。实例的账号在 `agent_instances.im_user_id` |
| `im_groups` | `id`、`workspace_id`、`group_id`、`name`、`announcement`、`owner_user_id`、`relay_enabled`、`max_agent_streak`、`max_per_minute`、`dismissed_at`、`version`、`created_at`、`updated_at` | 工作空间的群，随工作空间级联删除；`group_id` 是 OpenIM 群 ID（`ws` + 32 位十六进制，由建群的 Idempotency-Key 推出；会话 ID 为 `sg_<group_id>`），唯一；`owner_user_id` 是群主 → `users`（删除后置空）。防刷屏阈值直接放在群上（方案的 `im_group_policies` 合并进来），计数在 Redis：CHECK `max_agent_streak` 在 1–50 之间（默认 8：管理员上次发言后，实例最多连续几条），`max_per_minute` 在 1–60 之间（默认 6：单个实例每分钟几条）。`relay_enabled = false` 时，拒收实例 @ 其他实例的群消息；解散只记 `dismissed_at`，不删行；`version` 用于防刷屏设置的乐观并发。`project_id`（T-0110，迁移 0028）→ `projects`（删除后置空）：项目群；部分唯一索引 `uq_im_groups_project` 保证每个项目最多一个没有解散的群；项目群的群 ID 由（项目、第几个群）推出，智能体成员跟随 `project_agents` |
| `im_group_members` | `id`、`group_pk`、`im_user_id`、`instance_id`、`user_id`、`role`、`joined_at` | `group_pk` → `im_groups`、`instance_id` → `agent_instances`、`user_id` → `users`，都级联删除；CHECK `one_member`：`instance_id` 与 `user_id` 恰好一个有值（系统账号 `ws_notice` 进群但不记在这里）；`role` ∈ `owner` / `admin` / `member`；`(group_pk, im_user_id)` 唯一；索引 `ix_im_group_members_im_user_id`；成员只能是本工作空间的实例与 owner / admin，由服务层检查 |
| `im_webhook_inbox` | `id`、`event_key`、`command`、`payload_json`、`status`、`attempts`、`next_retry_at`、`received_at`、`processed_at`、`error_text` | OpenIM after 回调（发消息、上下线、删好友）的收件箱。回调没有签名，回调地址带口令，只接受来自 OpenIM 网段的请求；before 回调同步判断，不进收件箱。`event_key` 唯一（`msg:<serverMsgID>`、`online:` / `offline:<用户>:<平台>:<seq>`、`delete_friend:<owner>:<friend>:<收到的毫秒>`），重复送达只记录一次。`payload_json` 是回调原始请求体（上限 256 KiB），处理时只取其中的 ID，内容以 `pull_msg_by_seq` 拉回的为准。不关联工作空间，没有外键。`status` ∈ `pending` / `processing` / `retry` / `completed` / `dead_letter`，失败 8 次进 `dead_letter`；`next_retry_at` 是下次可领取的时间，处理中时是 2 分钟租约的到期时间；部分索引 `ix_im_webhook_pending`（`status IN ('pending','retry','processing')`）；`error_text` 只存错误分类。处理完超过 7 天的事件由 Worker 清理，`dead_letter` 保留 |

### 迁移 `0013`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_conversations` | `id`、`workspace_id`、`owner_user_id`、`conversation_id`、`kind`、`peer_instance_id`、`group_pk`、`pinned_at`、`muted`、`read_seq`、`max_seq`、`last_message_at`、`version`、`created_at`、`updated_at` | 每个管理员在一个工作空间里的会话，已读、置顶、免打扰各自独立；`(owner_user_id, conversation_id)` 唯一，`conversation_id` 另有索引；`conversation_id` 是 OpenIM 会话 ID（单聊 `si_`、群聊 `sg_`、通知 `sn_`）；`kind` ∈ `single` / `group` / `notice`，`notice` 是本工作空间的通知账号 `wn_<工作空间>` 发审批卡片的会话；`workspace_id`、`owner_user_id`（→ `users`）、`peer_instance_id`（→ `agent_instances`）、`group_pk`（→ `im_groups`）都级联删除。CHECK：`read_seq`、`max_seq` ≥ 0，两者只增不减；未读数 = 序号大于 `read_seq`、且不是自己发的消息条数，`muted` 的会话不计入顶栏未读。`pinned_at` / `muted` 先同步到 OpenIM，失败时本地不改；`version` 在修改置顶 / 免打扰时递增，接口不校验 |
| `im_messages` | `id`、`workspace_id`、`conversation_id`、`server_msg_id`、`client_msg_id`、`seq`、`sender_im_id`、`sender_instance_id`、`content_type`、`content_json`、`plain_text`、`at_im_ids`、`status`、`sent_at`、`created_at` | 消息投影，每条 OpenIM 消息一行，按 OpenIM 会话 ID 只存一份：群里的多个管理员共用，不关联 `im_conversations` 的行。序号与内容以 `pull_msg_by_seq` 拉回的为准，每个会话最多补最近 1000 条。**保存消息内容**：`content_json` 是解码后的 content（超过 64 KiB 或无法解析时为 `{}`），`plain_text` 是文字消息正文或系统消息说明；图片消息只有地址与尺寸，图片本身在 OpenIM 对象存储；文件、视频消息只留链接、名称、大小、类型、时长与封面这几个字段（T-0119，对账补进投影时不再清空它们）；通知与不支持的类型为 `{}`。`server_msg_id` 唯一，`(conversation_id, seq)`、`(conversation_id, client_msg_id)` 各自唯一；工作台代发、还没被 OpenIM 受理的消息 `seq` 与 `server_msg_id` 为空。`status` ∈ `sending` / `sent` / `failed` / `unknown`；`sender_instance_id` → `agent_instances`（删除后置空）；随工作空间级联删除；索引 `(conversation_id, sent_at)` 与 `sent_at`。保留 `IM_MESSAGE_RETENTION_DAYS`（默认 365 天），超期由 Worker 按 `sent_at` 删除，OpenIM 里的消息不受影响 |

### 迁移 `0014`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_message_pins` | `id`、`conversation_pk`、`server_msg_id`、`pinned_by`、`pinned_at` | 单条消息置顶，挂在当前用户的会话上，只在工作台记录；`conversation_pk` → `im_conversations`（级联删除）；`pinned_by` → `users`（删除后置空）；`(conversation_pk, server_msg_id)` 唯一；每个会话最多 10 条，由接口检查（`PIN_LIMIT_REACHED`）；按 `server_msg_id` 关联消息投影，没有外键，消息超过保留期被删除后，置顶不再显示 |
| `im_messages`（新增列） | `idempotency_key` | 可空 UUID 列，加唯一约束 `uq_im_messages_idempotency_key`，没有默认值；值是工作台代发时的 Idempotency-Key，写进 OpenIM 消息的 `ex`，发送结果未知时靠它在投影里找回那条消息 |

### 迁移 `0015`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_contact_groups` | `id`、`workspace_id`、`name`、`sort_order`、`version`、`created_at`、`updated_at` | 通讯录分组，属于工作空间，随工作空间级联删除；`(workspace_id, name)` 唯一（重名返回 409 `CONTACT_GROUP_EXISTS`）；CHECK `sort_order` 在 0–10000 之间；删除分组不影响实例与好友关系；`version` 用于乐观并发 |
| `im_contact_group_members` | `contact_group_id`、`instance_id` | 分组与实例的对应关系，联合主键，一个实例可以在多个分组里；随分组或实例级联删除；索引 `ix_im_contact_group_members_instance_id`；成员整体替换，只接受本工作空间的实例（服务层检查） |

### 迁移 `0016`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_media` | `id`、`workspace_id`、`uploaded_by`、`mime`、`size`、`width`、`height`、`object_name`、`status`、`conversation_id`、`server_msg_id`、`expires_at`、`created_at` | 管理员上传到 OpenIM 对象存储的图片，**数据库不存图片内容**；`id` 既是 upload_id 也是 media_id；随工作空间级联删除；`uploaded_by` → `users`（删除后置空）；`mime` 按文件头识别（PNG / JPEG / GIF / WebP，不超过 10 MiB）；CHECK `size`、`width`、`height` 都 > 0；`object_name`（`myws/<工作空间>/<id>.<扩展名>`）唯一。`status` ∈ `uploaded` / `used`，发出后记下所在的会话与消息。浏览器经 `/api/v1/im/media/{id}` 读取（上传者本人，或能看到那个会话的管理员）；实例经接入入口的 `/im/obj/m/{id}` 读取（只限 `used`）；两条路径都由工作台代理。部分索引 `ix_im_media_unused_expiry`（`status = 'uploaded'`）：没有用来发消息的上传，`IM_UPLOAD_TTL_MINUTES`（默认 60 分钟）后由 Worker 删除记录 |

### 迁移 `0017`（S6-02、S6-03，T-0057）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `ai_tool_executions` | `id`、`workspace_id`、`instance_id`、`instance_name`、`acting_user_id`、`tool_name`、`risk_level`、`status`、`summary`、`arguments`、`arguments_display`、`arguments_hash`、`target_version`、`source`、`expires_at`、`decided_by`、`decided_at`、`decision_reason`、`executed_at`、`result_summary`、`error_code`、`request_id`、`version`、`created_at` | MCP 工具调用记录与审批，每次调用一行；随工作空间级联删除；`instance_id`、`acting_user_id`、`decided_by` 在对应记录删除后置空，`instance_name` 留作说明。CHECK：`risk_level` ∈ `read` / `write` / `critical`，`status` ∈ `pending_approval` / `rejected` / `expired` / `executing` / `succeeded` / `failed` / `unknown` / `denied`。读工具直接记 `succeeded` / `failed`，权限不够记 `denied`；写工具与高危工具先记 `pending_approval`，**此时没有执行**（A14）。`arguments` 是规范化后的完整参数，执行时就用它：只在待审批期间保存（读工具与 `denied` 不存），处理完（执行结束、拒绝、过期）就清空；工作流里标为敏感的参数值先加密再存（`$sealed`，用途绑定本行与参数名）；从不对外返回。`arguments_display` 是脱敏后给人看的参数（敏感值为 `***`），长期保留。`arguments_hash` 用于去重，执行前也用它校验参数没被改过；配置了凭证密钥时是 HMAC-SHA256，否则是 SHA-256。部分唯一索引 `uq_ai_tool_executions_pending_args`（`status = 'pending_approval'`）保证同一实例、同一工具、同样参数同时只有一条待审批；每个实例最多 20 条待审批，由服务层检查。`expires_at` 为创建后 15 分钟，Worker 每分钟按部分索引 `ix_ai_tool_executions_pending_expiry` 把到期的改为 `expired`。`target_version` 是创建审批时目标任务 / 日程的版本，执行时版本变了就失败。`source` 是 `{conversation_id, message_server_id, triggered_by_im_id, inferred}`，MCP 请求不带会话信息时由工作台推断。`summary` 由工作台按参数生成，不用 AI 给的文字；`error_code` 只存错误码；`request_id` 是那次 HTTP 请求的 ID；`version` 用于乐观并发（审批接口的 `If-Match`）。索引 `(workspace_id, created_at)`、`(acting_user_id, created_at)`；目前没有清理任务 |

### 迁移 `0018`（T-0063）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_instances`（新增列） | `im_token_expires_at` | 可空，无默认值。实例启动时领取 IM 令牌，工作台按 OpenIM 返回的 `expireTimeSeconds` 记下到期时间（令牌有效期 30 天）；Worker 每天检查，到期前 7 天提醒管理员（`agent_im_token_expiring`）。每周的定时重启会换新令牌、刷新这一列 |

### 迁移 `0019`（T-0080，历史结构；T-0149 已移除）

`app_logins` 已在迁移 `0047` 中随 n8n 移除；以下描述记录原迁移，不代表当前可用的账号存储。

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `app_logins` | `id`、`workspace_id`、`app`、`username`、`password_ref`、`version`、`created_at`、`updated_at`、`updated_by` | Vikunja、n8n 管理界面的登录账号，给管理员看的备忘（工作台的 Connector 仍只用 API Token / API Key）；`(workspace_id, app)` 唯一，CHECK `app` ∈ `vikunja` / `n8n`；`username` 是 Vikunja 用户名或 n8n 登录邮箱；`password_ref` 可空，只存 `enc:v1` 引用（用途绑定 `app_login:<id>:password`），列表接口不返回，查看明文要单独请求并写审计；随工作空间级联删除，`updated_by` → `users`（删除后置空）；`version` 用于乐观并发。见 `docs/decisions/2026-11-02-app-logins.md`。`0020` 删除 `app = vikunja` 的行，CHECK 改为只允许 `n8n` |

### 迁移 `0020`（T-0082）

人类 2026-11-02 决定移除 Vikunja，项目与任务由工作台自己管理（单人版：没有负责人、指派、评论、按项目共享；权限沿用工作空间角色）。
见 `docs/decisions/2026-11-02-remove-vikunja.md`。

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `projects` | `id`、`workspace_id`、`title`、`description`、`archived`、`version`、`created_at`、`updated_at`、`created_by`、`im_group_status`、`im_group_owner_id`、`im_group_error` | 工作台自己的项目；随工作空间级联删除；`created_by` → `users`（删除后置空）；`description` 默认空字符串，`archived` 默认 false（列表默认不含已归档）；`(workspace_id, id)` 唯一，供任务的复合外键使用；索引 `ix_projects_workspace_archived`（`workspace_id`、`archived`）；`version` 是整数，每次修改加 1，用于乐观并发。项目群（T-0110，迁移 0028）：CHECK `im_group_status` ∈ `none`（没有项目群）/ `pending`（要建群或同步成员，Worker 每分钟重试，部分索引 `ix_projects_im_group_pending`）/ `ready`；`im_group_owner_id` 是群主 → `users`（删除后置空，届时改用 owner）；`im_group_error` 是上一次失败的错误类别（不含第三方原文）。这三列不参与 `version` |
| `tasks` | `id`、`workspace_id`、`project_id`、`title`、`description`、`done`、`done_at`、`priority`、`due_at`、`labels`、`version`、`created_at`、`updated_at`、`created_by` | 工作台自己的任务；`(workspace_id, project_id)` → `projects (workspace_id, id)`（复合外键，级联删除）：任务只能挂在同一工作空间的项目上，删除项目时连带删除任务；随工作空间级联删除；`created_by` → `users`（删除后置空）；CHECK `ck_tasks_priority`：`priority` 在 0–5 之间（0 未设置、1 低、2 中、3 高、4 紧急、5 立即）；`done_at` 是完成时间；`labels` 是 `text[]`，默认空数组（个数与长度由接口限制：最多 20 个、每个不超过 50 个字符）；索引 `ix_tasks_workspace_done_due`（`workspace_id`、`done`、`due_at`）、`ix_tasks_project`（`project_id`）；`version` 是整数，每次修改加 1，接口里是不透明字符串 |

同一迁移里的其他变化：

- **转换**：`external_resources` 里 provider = vikunja、未删除的项目与任务插入新表，**主键不变**（收藏关联、AI 审批记录里的 `task_id` 照旧有效）；
  找不到所属项目的任务放进每个工作空间一个「未分组任务」项目；优先级截到 0–5，标签取自投影的 `metadata_json`。
- **`resource_links.target_resource_id`**：去掉指向 `external_resources` 的外键，改为项目或任务的 ID（没有外键）；迁移时删掉指向不存在对象的关联，
  之后由 `projects`、`tasks` 上的触发器 `delete_resource_links` 在删除时清理。
- **删除**：provider = vikunja 的集成（级联删除投影、凭证、同步游标）、`webhook_inbox` 表（连同 `ix_webhook_pending`）、
  `integrations.webhook_secret_ref` 列、`app_logins` 里 Vikunja 的行（CHECK `ck_app_logins_app` 改为 `app = 'n8n'`）。
- **搜索**：`projects`、`tasks` 上的触发器 `search_index` 写 `search_documents`，provider = `local`、没有 `source_url`；
  项目的 `body` 是说明，任务的 `body` 是说明加标签、`context` 是项目名（项目改名时同步更新）；`search_index_external` 只处理 GitHub；迁移最后整体重建一次索引。
- **downgrade** 只恢复表结构（`webhook_inbox`、外键、`webhook_secret_ref` 列、旧的搜索函数），不恢复数据：工作台的项目与任务会被删除。
  按 D-0004 第 3 节由人类确认后合并。

### 迁移 `0024`（T-0098）

知识库（`docs/v2/KNOWLEDGE_PLAN.md`，人类 2026-11-03 批准）。所有表随工作空间级联删除；作者字段指向 `users` / `agent_instances`，删除后置空。

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `knowledge_spaces` | `id`、`workspace_id`、`kind`、`project_id`、`instance_id`、`name`、`description`、`agent_access`、`writes_enabled`、`deletes_paused_at`、`corrections_unseen`、`version`、`created_at`、`updated_at` | 知识库。CHECK `kind` ∈ `public` / `project` / `agent`，`agent_access` ∈ `read_write` / `read` / `none`；部分唯一索引 `uq_knowledge_spaces_public` 保证每个工作空间一个公共库；`project_id`、`instance_id` 各自唯一，项目 / 实例删除后置空（知识库保留，对智能体只读）；`(workspace_id, id)` 唯一，供笔记的复合外键使用。`agent_access` 对公共库是所有智能体的权限（默认 `read_write`），对智能体自己的库是**别的**智能体能否读（`read` / `none`，默认 `read`），项目库不用它（看 `project_agents`）。`name` 跟随项目名 / 实例名。`writes_enabled = false` 时智能体不能写这个库；`deletes_paused_at` 是删除超限自动暂停的时间（管理员恢复后清空）；`corrections_unseen` 是智能体还没看到的纠正条数。迁移为已有的工作空间、项目、实例补建；之后新建的在第一次访问时补上（`ensure_spaces`）|
| `project_agents` | `project_id`、`instance_id`、`workspace_id`、`access`、`created_at` | 参与项目的智能体，主键 `(project_id, instance_id)`；复合外键 `(workspace_id, project_id)` → `projects`、`(workspace_id, instance_id)` → `agent_instances`（级联删除），不能跨工作空间；CHECK `access` ∈ `read_write` / `read`；索引 `ix_project_agents_instance`。也是“项目的智能体成员”（T-0110）：加入项目时为 `read_write`，并被拉进项目群 |
| `knowledge_notes` | `id`、`workspace_id`、`space_id`、`folder`、`title`、`title_key`、`aliases`、`body`、`type`、`tags`、`review_at`、`pinned`、`created_by_user` / `_instance`、`updated_by_user` / `_instance`、`source_kind`、`conflict_note`、`version`、`deleted_at`、`deleted_by_user` / `_instance`、`purge_after`、`created_at`、`updated_at` | 笔记（Markdown 正文）。复合外键 `(workspace_id, space_id)` → `knowledge_spaces`（级联删除）。`title_key` 是规范化（全角转半角、合并空白、转小写）的标题，部分唯一索引 `uq_knowledge_notes_title`（`space_id, title_key`，未删除）；`uq_knowledge_notes_brief` 保证每个库最多一篇未删除的 `brief`。CHECK `type` ∈ 方案 3.5 的 15 种加 `correction`（《收到的纠正》，工作台维护）；`source_kind` ∈ `human` / `observation` / `task` / `conversation` / `external` / `system`。`aliases`、`tags` 是 `text[]`。原来的 `confirmed_by` / `confirmed_at`（人已确认）由迁移 0025 删除（T-0102）；`conflict_note` 是写入时发现的冲突 / 重复提醒，人编辑后清空。删除是软删除：`deleted_at` + `purge_after`（30 天后），Worker 按 `ix_knowledge_notes_purge` 彻底删除。`version` 是整数，每次修改加 1，用于乐观并发。另有 `ix_knowledge_notes_workspace_updated`、标题的 trigram 索引 `ix_knowledge_notes_title_trgm`（相似标题提醒）|
| `knowledge_note_revisions` | `id`、`workspace_id`、`note_id`、`version`、`title`、`body`、`properties`、`change`、`author_user`、`author_instance`、`reason`、`evidence`、`idempotency_key`、`request_hash`、`lines_added`、`lines_removed`、`created_at` | 每次保存一行完整快照，随笔记级联删除；CHECK `change` ∈ `create` / `update` / `delete` / `restore` / `revert` / `rename`；`properties` 是类型、标签、别名等属性的快照；`reason`、`evidence`（`[{kind, ref}]`）是智能体写入时给的理由和证据；部分唯一索引 `uq_knowledge_note_revisions_idempotency`（`author_instance, idempotency_key`）实现智能体写入的幂等，`request_hash` 校验重放的参数一致；`lines_added` / `lines_removed` 供变更记录显示 |
| `knowledge_links` | `id`、`workspace_id`、`source_note_id`、`target_note_id`、`target_text`、`target_key`、`target_space_key`、`target_resource_type`、`target_resource_id` | 从正文解析的 `[[链接]]`（代码块里的不算，每篇最多 500 条），保存时整体替换；随源笔记级联删除，目标笔记删除后置空（变成未解析）；`target_key` / `target_space_key` 是规范化的标题和库名；CHECK `target_resource_type` 为空或 ∈ `task` / `project` / `bookmark`（`[[task:<id>]]` 等，没有外键）；部分索引 `ix_knowledge_links_pending` 用于新建、改名的笔记补上指向它的链接 |
| `knowledge_brief_reads` | `project_id`、`instance_id`、`workspace_id`、`version`、`read_at` | 智能体读过的项目简报版本，主键 `(project_id, instance_id)`，随项目 / 实例级联删除 |
| `knowledge_read_stats` | `day`、`instance_id`、`note_id`、`workspace_id`、`reads` | 智能体每天读每篇笔记的次数（“被读次数”和智能体指标），主键 `(day, instance_id, note_id)`，随实例 / 笔记级联删除；索引 `ix_knowledge_read_stats_note` |
| `knowledge_guard_events` | `id`、`workspace_id`、`instance_id`、`instance_name`、`kind`、`detail`、`note_title`、`created_at` | 保护机制拦下的操作（显示在变更记录里）；CHECK `kind` ∈ `secret_blocked` / `delete_paused` / `rate_limited` / `writes_disabled`；`detail` 只写规则名（如“GitHub 令牌”），**不存被拦下的内容**；索引 `(workspace_id, created_at)` |

同一迁移里的其他变化：

- **`ai_tool_executions.risk_level`**：CHECK 增加 `knowledge`（`knowledge_write` / `knowledge_delete` 直接执行，不进审批，人类 2026-11-03 决定）。
- **`ai_tool_executions.risk_level`（迁移 0029）**：CHECK 再增加 `direct`（`claim_task` / `release_task` / `complete_task` 直接执行，不进审批，人类 2026-11-04 决定）；已有 `direct` 记录时迁移拒绝降级。
- **搜索**：`knowledge_notes` 上的触发器 `search_index`（`search_trigger_note`）写 `search_documents`，`resource_type = note`、provider = `local`，
  `body` 是别名、标签加正文，`context` 是知识库名；软删除时移出索引。`search_rebuild()` 加上笔记。智能体的通用 `search` 工具不搜笔记（用 `knowledge_search`）。
- **downgrade** 删除知识库相关的工具调用记录（`risk_level = knowledge`），恢复旧的 CHECK、`search_rebuild()`，删除触发器、函数、笔记的搜索文档和全部知识库表。

### 迁移 `0025`（T-0102）

取消“人已确认”（`docs/v2/TIMELINE_PLAN.md` 3.2，人类 2026-11-04 决定）：删除 `knowledge_notes.confirmed_by`（连同外键 `fk_knowledge_notes_confirmed_by_users`）与 `confirmed_at`。
检索不再按确认加权。**downgrade** 加回两列（全部为空）与外键，原来的确认记录不恢复。

ORM 模型与迁移的一致性由集成测试中的 `alembic check` 保证。

### 迁移 `0026`（T-0103）

知识库图片（`docs/v2/TIMELINE_PLAN.md` 第 4 节）。随工作空间级联删除；对象存储里的文件由 Worker 清理（工作空间被删时卷或桶里的 `kb/<workspace_id>/` 留着，运维自行清理）。

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `knowledge_files` | `id`、`workspace_id`、`sha256`、`mime`、`size`、`width`、`height`、`storage`、`replica`、`replica_state`、`replica_attempts`、`replica_error`、`created_by_user`、`created_at` | 上传的图片。唯一 `(workspace_id, sha256)`：同一工作空间同一内容一行，对象 key 是 `kb/<workspace_id>/<sha256>`。`storage` ∈ `local` / `r2` / `oss`（写入时的主存储，换了主存储以后旧图片仍从原处读）；`replica_state` ∈ `none` / `pending` / `done` / `failed`，Worker 每分钟复制 `pending`（部分索引 `ix_knowledge_files_replica_pending`），失败 10 次标为 `failed`；`replica_error` 只记错误分类或 S3 错误码 |
| `knowledge_file_refs` | `file_id`、`note_id`、`workspace_id`、`current`、`created_at` | 笔记引用了哪些图片，主键 `(file_id, note_id)`，随图片或笔记级联删除。保存笔记时按正文更新：正文里有的 `current = true`，以前引用过的保留为 `false`（版本历史里的图片不丢）。只记本工作空间的图片。没有任何引用、上传超过 24 小时的图片由 Worker 每天删除（主存储与副本都删） |

**downgrade** 删除两张表，存储里的文件不动。

### 迁移 `0027`（T-0104）

任务时间轴（`docs/v2/TIMELINE_PLAN.md` 第 2.2 节）。已有任务的新字段都为空（未分配、没有执行记录）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `tasks` 新增 | `planned_start_at`、`started_at`、`assignee_user_id`、`assignee_instance_id` | 计划开始；第一次开始执行的时间（由执行记录维护，不改 `version`）；执行者是成员或实例，`ck_tasks_assignee` 保证最多一个。执行者被删除时置空（`ON DELETE SET NULL`），工作空间与角色由应用校验 |
| `task_runs` | `id`、`workspace_id`、`task_id`、`executor_kind`、`user_id`、`instance_id`、`executor_name`、`started_at`、`ended_at`、`end_reason`、`started_by`、`ended_by`、`start_key`、`created_at` | 一段执行一行，随任务、工作空间级联删除。`executor_kind` ∈ `user` / `instance`；`end_reason` ∈ `done` / `paused` / `interrupted` / `cancelled`，与 `ended_at` 同时为空或同时有值（`ck_task_runs_ended`），`ended_at >= started_at`。部分唯一索引 `uq_task_runs_open`：同一个任务同时只有一段在执行。`start_key` 唯一：开始这段时的 Idempotency-Key。成员或实例被删除时 `user_id` / `instance_id` 置空，`executor_name` 保留开始时的名字 |
| `tasks` 新增（T-0111，迁移 0029） | `claim_mode`、`claim_scope`、`claim_opened_by` | 接取方式：CHECK `claim_mode` ∈ `assigned`（管理员指派，默认）/ `open`（可自由接取），`claim_scope` ∈ `all` / `project`；`claim_opened_by` 是设为自由接取的人 → `users`（删除后置空），用他的 IM 账号通知智能体。部分索引 `ix_tasks_claimable`：`open`、没完成、没有执行者的任务 |
| `task_events` | `id`、`workspace_id`、`task_id`、`kind`、`actor_kind`、`user_id`、`instance_id`、`actor_name`、`note`、`created_at` | 任务的动态，随任务、工作空间级联删除。CHECK `kind` ∈ `opened` / `claimed` / `released` / `completed` / `reopened`，`actor_kind` ∈ `user` / `instance` / `system`；`user_id`、`instance_id` 删除后置空，`actor_name` 保留当时的名字；`note` 是原因或完成说明（来自人或智能体，按不可信文字处理）。索引 `ix_task_events_task_created` |
| `agent_instances` 新增（T-0112，迁移 0030） | `project_admin_enabled`、`project_admin_by`、`project_admin_at` | 项目管理权限：默认 false；打开的人 → `users`（删除后置空，届时项目群改用 owner 当群主）与时间；不参与 `version` 以外的乐观并发（开关接口按实例的 `version`） |
| `projects` 新增（T-0112，迁移 0030） | `created_by_instance` | 智能体建的项目 → `agent_instances`（删除后置空）：它只能把智能体拉进自己建的项目 |
| `agent_usage_minutes` | `instance_id`、`minute`、`workspace_id`、`input`、`output`、`cache_read`、`cache_write` | 每个实例每分钟的 token 增量（bigint），主键 `(instance_id, minute)`，索引 `(workspace_id, minute)`。只存有增量的分钟。随实例、工作空间级联删除；Worker 每小时删除 90 天前的 |
| `agent_usage_cursors` | `instance_id`、`day`、`session_hash`、`input`、`output`、`cache_read`、`cache_write`、`updated_at` | 每个实例、每天（UTC）、每个会话最后一次上报的累计值，主键 `(instance_id, day, session_hash)`，用来算增量。`session_hash` 是接入包算好的会话 key 的 SHA-256，工作台不存会话 key。Worker 删除 3 天前的 |
| `agent_instances` 新增 | `usage_reported_at` | 最近一次收到用量上报；为空表示没上报过（时间轴显示“未上报”；第一次上报只记基准） |
| `agent_host_samples`（T-0113，迁移 0031；**迁移 0045 删除**，见下文「迁移 `0045`」） | `instance_id`、`minute`、`workspace_id`、`cpu_percent`、`load1` / `load5` / `load15`、`cores`、`mem_total` / `mem_available`、`swap_total` / `swap_used`、`data_total` / `data_free`、`root_total` / `root_free`、`rx_rate` / `tx_rate` | 每个实例每分钟一条服务器状态样本，主键 `(instance_id, minute)`，随实例、工作空间级联删除；CPU 使用率与网速由两次上报的累计值之差算出，第一次或计数器变小时为空。保留 30 天（每天的清理任务） |
| `agent_host_state`（T-0113，迁移 0031） | `instance_id`、`workspace_id`、`reported_at`、`cpu_model`、`total_ticks` / `idle_ticks`、`rx_bytes` / `tx_bytes`、`uptime_seconds`、`mem_limit`、`latest`（JSONB）、`alerts`（JSONB） | 每个实例一行：上一次的累计值（算差用）、CPU 型号、开机时长、容器内存上限、最近一次的当前值、正在提醒的告警及开始时间（同一类告警恢复前只提醒一次）；随实例、工作空间级联删除 |
| `agent_ops_actions`（T-0114，迁移 0032） | `id`、`workspace_id`、`instance_id`、`action`、`service`、`status`、`exit_code`、`output`、`truncated`、`error_code`、`error_message`、`started_by`、`started_at`、`finished_at`、`expires_at` | 一次运维动作：CHECK `action` ∈ `status` / `logs` / `restart` / `up` / `prune` / `report`，`service` 为空或 ∈ `openclaw` / `tunnel` / `image-proxy`，`status` ∈ `running` / `succeeded` / `failed` / `interrupted`；`output` 是脱敏后的输出（最多 64 KB）；部分唯一索引保证同一实例同时只有一个 `running`；`expires_at` = 开始时间 + 7 天，Worker 每天删除过期记录（审计保留）；随实例、工作空间级联删除，`started_by` → `users`（删除后置空） |

**downgrade** 删除三张表和新增的字段：执行记录与 token 用量随之删除（都是 0027 之后才有的数据）。

### 迁移 `0033`（T-0117）

IM 会话目录（`docs/v2/AGENT_MESSAGING_PLAN.md` 第 3.1 节）。方案原本写这一步不需要迁移，为了判断“管理员回你的私聊你收不收得到”加了一列。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_instances` 新增 | `bundle_whitelist` | `text[]`，可空：签发接入包时写进 OpenClaw 配置 `inboundWhitelist` 的 IM 用户 ID（当时的 owner / admin 与全部实例）。`im_list_targets` 据此给出 `replies_reach_you`；为空表示接入包是本迁移之前签发的，无法判断 |

**downgrade** 删除这一列。

### 迁移 `0034`（T-0118）

智能体的发件箱与拒收记录（方案第 5、6 节）。都随工作空间、实例级联删除。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_im_outbox` | `id`、`workspace_id`、`instance_id`、`target`、`conversation_id`、`client_key`、`part`、`request_hash`、`content_type`、`content_json`（JSONB）、`plain_text`、`at_im_ids`、`media_id`、`status`、`source`、`if_rate_limited`、`send_at`、`next_attempt_at`、`deadline_at`、`attempts`、`error_code`、`error_message`、`server_msg_id`、`sent_at`、`created_at`、`updated_at` | 智能体经 `im_send` 主动发的消息，一条 OpenIM 消息一行（先文字、再每个附件各一行，`part` 从 0 起）。`target` 是 `user:<IM 用户 ID>` / `group:<群 ID>`；`content_type` 是 OpenIM 的消息类型（101 文字、106 @ 文字、102 图片、104 视频、105 文件），`content_json` 是要发的元素，`media_id` 指向 `im_media`（没有外键）。唯一 `(instance_id, client_key, part)`：同一个幂等键重试不会发出两次，`request_hash` 核对内容是否相同。CHECK `status` ∈ `scheduled` / `queued` / `sending` / `sent` / `rejected` / `failed` / `unknown` / `cancelled`，`source` ∈ `tool` / `reply_retry`（回调 5002 拒收后补发）/ `wakeup`，`if_rate_limited` ∈ `queue` / `fail`。`deadline_at` 是限流排队、补发的最晚时间；`error_code` 是拒收码或错误类别。部分索引 `ix_agent_im_outbox_due`（`next_attempt_at`，`status` ∈ `scheduled` / `queued` / `unknown`）供 Worker 取到期的；`ix_agent_im_outbox_instance_created`。发出时 `ex` 里带发件箱 ID，结果未知时据此在 `im_messages.idempotency_key` 里对账。已结束的行保留 30 天 |
| `agent_im_rejections` | `id`、`workspace_id`、`instance_id`、`conversation_id`、`code`、`content_type`、`client_msg_id`、`preview`、`retried`、`created_at` | 实例发的消息被 before 回调拒收的记录（主要是插件发的自动回复；`im_send` 发的由发件箱自己记）。`preview` 只存脱敏后的前 120 字；`retried` 表示工作台已排队补发。唯一 `(instance_id, client_msg_id)`（回调重放不重复记）；索引 `(instance_id, created_at)`。保留 7 天 |

**downgrade** 删除两张表（都是 0034 之后才有的数据）。

### 迁移 `0035`（T-0119）

IM 附件：文件与视频（方案第 4 节），以及实例的附件开关（D3）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_media` 新增 | `kind`、`file_name`、`duration_seconds`、`sha256`、`instance_id` | 从“只有图片”扩展到图片 / 文件 / 视频：CHECK `kind` ∈ `image`（默认，已有的行）/ `file` / `video`；尺寸约束 `ck_im_media_dimensions` 放宽为 `size > 0`、宽高 ≥ 0，只有图片要求宽高 > 0（文件与视频宽高为 0）。`file_name` 是文件与视频显示的文件名，`duration_seconds` 是视频时长（从 `mvhd` 读出），`sha256` 是内容哈希。`instance_id` → `agent_instances`（删除后置空）：智能体经 `im_send` 发的附件（对象名 `myws/<工作空间>/agent/<ID>.<扩展名>`）；管理员上传的仍记 `uploaded_by` |
| `agent_instances` 新增 | `im_media_enabled`、`im_media_by`、`im_media_at` | 附件权限：默认 false；打开的人 → `users`（删除后置空）与时间，关闭时清空。开关接口按实例的 `version` 乐观并发 |

**downgrade** 先删除 `kind` 不是 `image` 的媒体行（文件与视频是 0035 之后才有的数据），再恢复原来的尺寸约束、删除新增的列与外键。

### 迁移 `0036`（T-0121）

唤醒（方案第 7.2 节）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_wakeups` | `id`、`workspace_id`、`instance_id`、`source`、`created_by`、`target`、`session_key`、`prompt`、`run_at`、`repeat`、`time_zone`、`status`、`runs`、`claimed_at`、`last_run_at`、`last_error`、`created_at`、`updated_at` | 到点叫醒智能体跑一轮；随工作空间、实例级联删除，`created_by` → `users`（删除后置空，管理员建的才有）。CHECK `source` ∈ `agent`（智能体用 `schedule_wakeup` 安排）/ `admin`（迁移 0039 再加 `issue`），`repeat` ∈ `none` / `daily` / `weekdays` / `weekly`（按 `time_zone` 的同一个钟点，默认 `UTC`），`status` ∈ `pending` / `running` / `done` / `failed` / `cancelled`，`prompt` 1 ~ 2000 字。`target` 为空是独立会话；`session_key` 是 OpenClaw 的会话键（与 openclaw-channel 的写法一致）。重复的唤醒每跑一次 `runs` 加 1、`run_at` 改成下一次；`last_error` 只存一行原因。部分索引 `ix_agent_wakeups_due`（`instance_id, run_at`，`status = pending`）、`ix_agent_wakeups_instance_created`。待执行的个数（智能体 20、管理员 50）由应用限制；领取后 15 分钟没有结果记为失败；结束 30 天后删除 |

**downgrade** 删除这张表（都是 0036 之后才有的数据）。

### 迁移 `0037`（T-0126）

终止智能体正在执行的任务（方案第 7.3 节），以及实例的“终止其他智能体任务”开关（D20）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_run_stops` | `id`、`workspace_id`、`instance_id`、`requested_by`、`requested_by_instance`、`task_id`、`reason`、`status`、`soft_until`、`aborted`、`error`、`claimed_at`、`finished_at`、`created_at`、`updated_at` | 终止令；随工作空间与被叫停的实例级联删除。下令的人 `requested_by` → `users`、下令的智能体 `requested_by_instance` → `agent_instances`、`task_id` → `tasks`，都是删除后置空。CHECK `status` ∈ `pending` / `running` / `done` / `failed` / `expired`，`reason` 1 ~ 500 字。`soft_until` 是软停止的截止时间（下令后 5 分钟，期间这个实例的 MCP 调用返回 `RUN_STOPPED`）；`aborted` 是硬停止中止的会话数。10 分钟没被领取记为 `expired`，领取后 5 分钟没有结果记为 `failed`。索引 `(instance_id, created_at)`；已结束、下令超过 30 天的由 Worker 删除 |
| `agent_instances` 新增 | `stop_others_enabled`、`stop_others_by`、`stop_others_at` | “终止其他智能体任务”的权限：默认 false；打开的人 → `users`（删除后置空）与时间，关闭时清空 |

**downgrade** 删除这张表和新增的三列（都是 0037 之后才有的数据）。

### 迁移 `0038`（T-0122）

项目与任务的 README（方案第 9 节，D16）。已有的项目与任务由 Worker 补建，迁移里不写笔记。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `knowledge_notes.type` | — | CHECK `ck_knowledge_notes_type` 增加 `readme`（工作台生成，人和智能体不能新建这种类型、不能把它改成别的类型、不能移动或删除） |
| `knowledge_notes` 新增 | `task_id` | 任务 README 属于哪个任务 → `tasks`（删除后置空；工作台同时把这篇 README 放进回收站）。CHECK `ck_knowledge_notes_task_readme`：只有 `readme` 才有 `task_id`。部分唯一索引 `uq_knowledge_notes_task_readme`（`task_id`，未删除）保证每个任务最多一篇；`uq_knowledge_notes_project_readme`（`space_id`，`type = readme`、没有 `task_id`、`folder = ''`、未删除）保证每个项目知识库最多一篇项目 README |

**downgrade** 先删除全部 `readme` 笔记（连同版本与链接，外键级联；**人和智能体在 README 里补充的内容也一起删除**），再删除索引、约束与列，恢复原来的类型约束。

### 迁移 `0039`（T-0123）

项目里的 Issues（方案第 8 节）。人和智能体两种作者 / 参与者都用一对 `user_id` / `instance_id` 表示。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `issues` | `id`、`workspace_id`、`project_id`、`number`、`title`、`body`、`kind`、`labels`、`state`、`close_reason`、`duplicate_of`、`locked`、`author_user_id`、`author_instance_id`、`author_name`、`closed_at`、`agent_streak`、`comment_count`、`last_activity_at`、`deleted_at`、`version`、`created_at`、`updated_at` | 随工作空间、项目级联删除。唯一 `(project_id, number)`：按项目编号（开的时候锁住项目行取最大编号加一）。CHECK `kind` ∈ `question` / `proposal` / `bug` / `contract` / `handoff` / `review` / `blocker` / `other`，`state` ∈ `open` / `closed`，`close_reason` 为空或 ∈ `resolved` / `wontfix` / `duplicate`，而且关闭时必须有原因、打开时必须没有（`ck_issues_closed`）；标题 1 ~ 200 字、正文 ≤ 20000 字、标签 ≤ 10 个；作者最多一个（`ck_issues_one_author`），作者删除后置空、`author_name` 保留当时的名字；`duplicate_of` → `issues`（删除后置空）。`agent_streak` 是连续来自智能体的评论数（人评论后归零，到 10 暂停智能体评论）。`deleted_at` 是软删除。`version` 用于乐观并发。索引 `ix_issues_project_state`（`project_id, state, number`）、`ix_issues_workspace_activity` |
| `issue_assignees` | `id`、`issue_id`、`user_id`、`instance_id`、`created_at` | 指派（人或智能体，`ck_issue_assignees_one` 保证恰好一个）；随 Issue、人、实例级联删除；部分唯一索引分别保证同一个人 / 同一个实例只指派一次；索引 `ix_issue_assignees_instance` |
| `issue_tasks` | `issue_id`、`task_id`、`created_at` | Issue 与任务的关联，主键 `(issue_id, task_id)`，两边删除都级联；索引 `ix_issue_tasks_task` |
| `issue_comments` | `id`、`workspace_id`、`issue_id`、`reply_to`、`body`、`author_user_id`、`author_instance_id`、`author_name`、`edited_at`、`hidden_at`、`hidden_by`、`created_at` | 评论，随工作空间、Issue 级联删除；CHECK 正文 1 ~ 10000 字、作者最多一个；`reply_to` → 同表（删除后置空）；隐藏只记 `hidden_at` / `hidden_by`（→ `users`，删除后置空），不物理删除。索引 `(issue_id, created_at)` |
| `issue_events` | `id`、`workspace_id`、`issue_id`、`kind`、`actor_kind`、`user_id`、`instance_id`、`actor_name`、`data`（JSONB）、`created_at` | 时间线事件；CHECK `kind` ∈ `opened` / `closed` / `reopened` / `assigned` / `unassigned` / `labeled` / `unlabeled` / `linked` / `unlinked` / `retitled` / `locked` / `unlocked` / `mentioned`（在别的 Issue 里被 `#编号` 提到），`actor_kind` ∈ `user` / `instance` / `system`；人、实例删除后置空，`actor_name` 保留。索引 `(issue_id, created_at)` |
| `issue_mentions` | `id`、`issue_id`、`comment_id`、`user_id`、`instance_id`、`created_at` | `@名字` 提到的人或智能体（恰好一个），`comment_id` 为空表示在 Issue 正文里；都是级联删除。索引 `(instance_id, created_at)`、`(user_id, created_at)`，用于收件箱 |
| `issue_reads` | `id`、`issue_id`、`user_id`、`instance_id`、`read_at` | 每个参与者对每个 Issue 读到哪里（恰好一个，部分唯一索引）；用于未读与收件箱 |
| `projects` 新增 | `issue_notices` | 默认 false：Issue 新开、关闭时在项目群里发一行提示 |
| `agent_wakeups.source` | — | CHECK 增加 `issue`：Issue 有新动态时工作台安排的合并唤醒 |

同一迁移里的其他变化：

- **搜索**：`issues` 上的触发器 `search_index`（`search_trigger_issue` → `search_index_issue`）写 `search_documents`，`resource_type = project_issue`、provider = `local`，标题是“#编号 标题”，`body` 是标签加正文，`context` 是项目名；软删除时移出索引。`search_rebuild()` 加上 Issue。

**downgrade** 恢复旧的 `search_rebuild()`，删除触发器、函数与 `project_issue` 的搜索文档，删除 `source = issue` 的唤醒并恢复 `agent_wakeups.source` 的约束，删除 `projects.issue_notices` 和全部 Issue 相关的表（都是 0039 之后才有的数据）。

### 迁移 `0040`（T-0128）

Gmail 邮件（`docs/v2/MAIL_PLAN.md` 第 4 节）。只新增表与字段；邮件正文不入库。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `mail_oauth_clients` | `id`、`workspace_id`、`client_id`、`client_secret_ref`、`updated_by`、`version` | 每个工作空间一条（唯一 `workspace_id`）；`client_secret_ref` 是加密后的密钥（`enc:v1:`，加密用途绑定工作空间）；随工作空间级联删除，`updated_by` → `users`（删除后置空） |
| `mail_accounts` | `id`、`workspace_id`、`email`、`display_name`、`scopes`、`refresh_token_ref`、`status`、`last_error`、`agent_signature`、`connected_by`、`connected_at`、`version` | 连接的 Gmail 邮箱，唯一 `(workspace_id, email)`（断开后重新连接复用这一行）。`scopes` 只记实际授权的 `gmail.readonly` / `gmail.send`；`refresh_token_ref` 加密（用途绑定邮箱 ID），断开后清空；CHECK `status` ∈ `active` / `needs_reconnect` / `disconnected`；`agent_signature` 默认 true（原有的 `agents_allowed` 在 0041 删除）。access token 只加密放 Redis（`mail_at:{id}`，按 `expires_in` 提前 60 秒过期），不入库 |
| `mail_sends` | `id`、`workspace_id`、`account_id`、`actor_kind`、`user_id`、`instance_id`、`actor_name`、`sender`、`to_addresses`、`cc_addresses`、`bcc_count`、`subject`、`body_chars`、`reply_to_message_id`、`gmail_message_id`、`gmail_thread_id`、`status`、`error_code`、`client_key`、`created_at`、`finished_at` | 发件记录（人或智能体），**不存正文**（只记 `body_chars`），密送只记人数。CHECK `actor_kind` ∈ `user` / `instance`、`user_id` 与 `instance_id` 至多一个、`status` ∈ `sending` / `sent` / `failed` / `unknown`、主题 ≤ 200 字。部分唯一索引 `uq_mail_sends_client_key (instance_id, client_key)`：智能体的防重复键。索引按工作空间、实例与时间。邮箱、成员、实例删除后对应字段置空，`actor_name`、`sender` 保留当时的值 |
| `agent_instances` 新增 | `mail_read_enabled`、`mail_send_enabled`、`mail_send_scope`、`mail_send_recipients`、`mail_send_by`、`mail_send_at` | 实例的邮件权限：查询默认 true、发送默认 false；CHECK `mail_send_scope` 为空或 ∈ `any` / `listed`，打开发送时必须有范围（`ck_agent_instances_mail_send_needs_scope`），收件人列表最多 50 条；`mail_send_by` → `users`（删除后置空） |

**downgrade** 删除三张表和新增的字段（都是 0040 之后才有的数据）。

### 迁移 `0041`（T-0132）

邮箱按智能体分配（`docs/v2/MAIL_PLAN.md` 第 10 节）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_mail_accounts` | `workspace_id`、`instance_id`、`account_id`、`assigned_by`、`created_at` | 哪个邮箱分配给哪个实例；主键 `(instance_id, account_id)`，索引 `account_id`。复合外键 `(workspace_id, instance_id)` → `agent_instances`、`(workspace_id, account_id)` → `mail_accounts`（保证同一个工作空间，随实例 / 邮箱级联删除）；`assigned_by` → `users`（删除后置空）。断开邮箱时删除它的分配 |
| `mail_accounts` | 新增唯一 `(workspace_id, id)`；删除 `agents_allowed` | 唯一约束供上面的复合外键用 |

升级时把 `agents_allowed = true` 且没断开的邮箱分配给同一工作空间现有的全部实例（升级前后智能体能看到的邮箱不变）。
**downgrade** 加回 `agents_allowed`（有分配的邮箱为 true，其余 false），删除分配表与唯一约束；按实例的分配细节不保留。

### 迁移 `0042`（T-0136 ~ T-0139）

运行服务器（`docs/v2/RUNNER_PLAN.md` 第 12 节）。只新增表与字段；环境、模板、运行都带工作空间（`environments` 用复合外键 `(workspace_id, host_id)` → `runner_hosts`）。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `runner_hosts` | `id`、`workspace_id`、`name`、`status`、`ingress`、`token_hash`、`token_prefix`、`cert_fingerprint`、`cpus`、`mem_bytes`、`disk_bytes`、`disk_free_bytes`、`kvm`、`runtimes`、`docker_version`、`agent_version`、`report`、`policy`、`last_seen_at`、`created_by`、`version` | 每个工作空间一台（唯一 `workspace_id`）；凭证只存 SHA-256（唯一）与前 12 个字符；CHECK `status` ∈ `pending` / `online` / `offline` / `disabled`、`ingress` ∈ `intranet` / `public`；`policy` 是回收策略的覆盖值 |
| `runner_templates` | `id`、`workspace_id`、`name`、`description`、`owner_instance_id`、`created_by`、`status`、`revision`、`spec`、`build_status`、`build_error`、`build_log`、`image_ref`、`image_digest`、`image_bytes`、`build_seconds`、`built_at`、`last_used_at`、`use_count`、`version` | 唯一 `(workspace_id, name)`，名称 1 ~ 64 字；`spec` 是规整后的规格（镜像、安装步骤、运行时、用户、网络与域名、资源、环境变量、密钥**名称**、要保存的目录）；CHECK `status`、`build_status` ∈ `pending` / `building` / `ready` / `failed`；建立者是实例或用户（删除后置空） |
| `runner_environments` | `id`、`workspace_id`、`host_id`、`template_id`、`template_revision`、`owner_instance_id`、`purpose`、`lifetime`、`reuse_key`、`state`、`state_reason`、`spec`、`setup`、`share`、`project_id`、`pinned`、`container_name`、`volume_name`、`network_name`、`snapshot_*`、`start_seconds`、`rebuild_seconds`、`use_count`、`mem_bytes`、`disk_bytes`、`cpu_used_cores`、`cpu_window_seconds`、`cpu_reported_at`、`mem_reported_at`、`disk_reported_at`、`egress`、时间字段、`delete_after`、`version` | `spec` 是**配置记录**（建这个环境时的完整规格与镜像 digest）；CHECK `lifetime` ∈ `once` / `session` / `persistent`、`state` ∈ `creating` / `running` / `stopped` / `archived` / `pending_delete` / `deleted` / `failed`、`share` ∈ `none` / `project`（共享时必须有 `project_id`）；索引 `(workspace_id, state)`、`(owner_instance_id, reuse_key)`；容器 / 数据卷 / 网络名是 `mw-env|vol|net-<12 位十六进制>` |
| `runner_delete_conditions` | `id`、`environment_id`、`kind`、`ref_id`、`at`、`seconds`、`created_by_instance_id`、`fired_at` | 删除条件；CHECK `kind` ∈ `run_finished` / `task_done` / `issue_closed` / `project_archived` / `at` / `idle` / `released`；部分索引 `(kind, ref_id) WHERE fired_at IS NULL`；随环境级联删除 |
| `runner_runs` | `id`、`workspace_id`、`environment_id`、`instance_id`、`command`、`status`、`exit_code`、`timeout_seconds`、`output`、`output_truncated`、`wake`、时间字段 | 一次命令：`command` 脱敏后记录；`output` 最多 1 MB（开头四分之一加结尾，脱敏）；CHECK `status` ∈ `queued` / `running` / `succeeded` / `failed` / `timeout` / `unknown` / `cancelled`；`wake` 表示结束后叫醒智能体 |
| `runner_commands` | `id`、`workspace_id`、`host_id`、`kind`、`payload`、`status`、`result`、`error`、`attempts`、`environment_id`、`template_id`、`run_id`、`created_at`、`sent_at`、`finished_at`、`deadline_at` | 下发给 runner-agent 的指令（按 `id` 去重）；**`payload` 不含密钥值**（下发时才解密）；`get_files` 之外的 `result` 不存文件内容；CHECK `status` ∈ `pending` / `sent` / `done` / `failed` / `unknown` / `cancelled` |
| `agent_instances` 新增 | `runner_enabled`、`runner_by`、`runner_at`、`runner_limits` | 运行环境权限（默认关闭）；`runner_limits` 是管理员改过的限额（空用默认值） |
| `agent_wakeups.source` | 增加 `runner` | 长时间运行结束后叫醒智能体 |

**downgrade** 删除 `source = runner` 的唤醒并恢复约束，删除新增的表与字段（都是 0042 之后才有的数据）。

### 迁移 `0043`（T-0139）

常驻环境的快照（`docs/v2/RUNNER_PLAN.md` 第 6.5 节）。只新增一张表。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `runner_snapshots` | `id`、`workspace_id`、`environment_id`、`status`、`key_ref`、`store`、`entries`、`parts`、`bytes`、`error`、`created_at`、`ready_at`、`expires_at` | “要保存的目录”打成 tar、分块（≤ 16 MB）加密后存在对象存储（`store` 是 `local` 或 S3 兼容，key `runner/<工作空间>/<快照>/<块号>`）；`key_ref` 是这份快照的数据密钥（AES-256-GCM），用凭证密钥封存、用途绑定快照 ID，**不存明文**；`entries` 是每个目录的起始块、块数与字节数；CHECK `status` ∈ `uploading` / `ready` / `failed` / `deleted`；索引 `(environment_id, created_at)`、`(expires_at)`；随环境、工作空间级联删除。环境删除后快照保留 `archive_retention_days`（默认 30）天，到期由每分钟检查删掉对象存储里的块 |

**downgrade** 删除这张表；对象存储里已有的快照块不会自动删除（都在前缀 `runner/<工作空间 ID>/` 下，需要时按前缀手动清理）。

### 迁移 `0044`（T-0136）

页面上通过 SSH 部署运行服务器（`docs/v2/RUNNER_PLAN.md` 第 9 节），与 OpenClaw 实例的 `agent_ssh_targets` / `agent_ssh_jobs`（迁移 0021 ~ 0023）列相同，只是挂在运行服务器上。只新增两张表。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `runner_ssh_targets` | `id`、`workspace_id`、`host_id`、`host`、`address`、`port`、`username`、`host_key_algorithm`、`host_key`、`host_key_fingerprint`、`host_key_confirmed_at/_by`、`client_key_ref`、`client_public_key`、`install_dir`、`installed_at`、`version`、`created_by`、时间字段 | 复合外键 `(workspace_id, host_id)` → `runner_hosts`（级联删除）；唯一 `host_id`（每台运行服务器最多一个）；CHECK `port` 1 ~ 65535；**登录密码从不保存**；工作台专用私钥以 `enc:v1:` 引用保存，用途绑定 `runner_ssh:<id>` |
| `runner_ssh_jobs` | `id`、`workspace_id`、`host_id`、`target_id`、`kind`、`status`、`steps`、`log`、`error_code`、`error_message`、`started_by`、`started_at`、`finished_at`、`updated_at` | CHECK `kind` ∈ `install` / `uninstall`、`status` ∈ `running` / `succeeded` / `failed` / `interrupted`；部分唯一索引 `(target_id) WHERE status = 'running'`（同一目标同时一个任务）；`log` 是命令输出的结尾（最多 64 KB），密码、运行服务器凭证、私钥已替换成 `***`；API 重启时 `running` 改为 `interrupted` |

**downgrade** 删除这两张表（服务器上已装的 runner-agent 与专用公钥不受影响）。

### 迁移 `0045`（T-0144 / T-0146）

一台服务器部署多个 OpenClaw（`docs/v2/MULTI_OPENCLAW_PLAN.md` 第 6 节）。人类 2026-11-07 确认还没有部署过（D5、D6），**不迁移旧数据**：按实例的旧样本表直接删除，由服务器级与实例级两张样本表代替。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_servers`（新） | `id`、`workspace_id`、`name`、`kind`、`machine_key`、`reported_at`、`cpu_model`、`uptime_seconds`、`latest`（JSONB）、`alerts`（JSONB）、`version`、时间字段 | 一台 OpenClaw 服务器；CHECK `kind` ∈ `auto`（按上报的服务器指纹自动识别）/ `ssh`（管理员添加，T-0145）；部分唯一索引 `uq_agent_servers_machine (workspace_id, machine_key) WHERE machine_key IS NOT NULL`。`machine_key` 是 `boot_id` 的 HMAC-SHA256（盐按工作空间，接入包的 `MYWS_HOST_SALT`），**不存 `boot_id` 原值**；机器重启后跟着换。`latest` / `reported_at` 是整机最近一次的数值，`alerts` 是整机正在提醒的告警；随工作空间级联删除 |
| `agent_server_samples`（新） | `server_id`、`minute`、`workspace_id`、`cpu_percent`、`load1` / `load5` / `load15`、`cores`、`mem_total` / `mem_available`、`swap_total` / `swap_used`、`data_total` / `data_free`、`root_total` / `root_free` | 主键 `(server_id, minute)`：同一台服务器上的每个实例都会报，先到的写入、后到的忽略；索引 `minute`；随服务器、工作空间级联删除；保留 30 天。**没有网速**：每个实例有自己的网卡，整机网速是同一分钟各实例网速之和（读的时候从下表算） |
| `agent_instance_samples`（新） | `instance_id`、`minute`、`workspace_id`、`server_id`、`cpu_cores`、`cpu_percent`、`mem_bytes`、`mem_limit`、`pids`、`data_bytes`、`rx_rate`、`tx_rate` | 每个实例每分钟一条自己容器的样本（cgroup v2 / v1）：CPU 核数与占整机的百分比、内存已用（不含可回收的文件缓存）与上限、进程数、数据目录大小、自己网卡的速率；主键 `(instance_id, minute)`，同一分钟再报就覆盖；索引 `minute`、`(server_id, minute)`；随实例、工作空间级联删除，`server_id` → `agent_servers`（删除后置空）；保留 30 天 |
| `agent_host_state` 新增 | `cpu_usec`、`machine_key`、`container`（JSONB） | 容器 CPU 用量累计（微秒，算差用；变小记一次容器重启）、这个实例看到的服务器指纹、自己容器的当前值（含数据目录大小与最近一小时的重启时间）；原有的 `alerts` 改为只记这个实例的告警（`mem_near_limit` / `restarting` / `cpu_hog`），整机告警在 `agent_servers.alerts` |
| `agent_instances` 新增 | `server_id`、`server_manual`、`mem_limit_mb`、`cpus` | 所在服务器 → `agent_servers`（删除后置空）；`server_manual` 默认 false，为 true 时按管理员指定、不再按指纹自动改；资源上限（D2：默认 null 不限制），CHECK `mem_limit`：null 或 256 ~ 1048576（MiB），CHECK `cpus`：null 或 0.1 ~ 256，写进接入包的 `compose.yaml`，重新部署后生效 |
| `agent_host_samples` | — | 删除（D6） |

**downgrade** 回到 0044 的结构，同样不保留数据：删除三张新表与新增的列，重建空的 `agent_host_samples`（服务器上的实例不受影响，下一次上报重新写入）。

### 迁移 `0046`（T-0144 / T-0145 / T-0147）

一台服务器部署多个 OpenClaw 的 SSH 部分（`docs/v2/MULTI_OPENCLAW_PLAN.md` 第 4、8 节）：SSH 登录信息与专用密钥按服务器（一台一把，D7），每个实例的部署单独记。人类 2026-11-07 确认还没有部署过（D5），**不迁移旧数据**：升级前删除按实例的 SSH 目标、SSH 任务与运维记录。

| 表 / 字段 | 主要字段 | 约束与说明 |
|---|---|---|
| `agent_ssh_targets` 改 | 去掉 `instance_id`、`install_dir`、`installed_at`；新增 `server_id`、`prepared_at` | 每台 SSH 服务器一条：`server_id` → `agent_servers`（级联删除），唯一；`prepared_at` 是准备好（专用公钥装上、Docker 可用、专用密钥能登录）的时间。其余列（地址、主机公钥、确认、专用私钥引用）不变，**登录密码从不保存** |
| `agent_deployments`（新） | `id`、`workspace_id`、`instance_id`、`server_id`、`install_dir`、`installed_at`、`version`、`created_by`、时间字段 | 实例部署在哪台服务器、哪个目录（`~/myworkspace-openclaw/<实例短号>`）；复合外键 `(workspace_id, instance_id)` → `agent_instances`（级联删除），唯一 `instance_id`（一个实例同时只部署在一台服务器上）；`server_id` → `agent_servers`（RESTRICT：还有部署的服务器不能删）；索引 `server_id`；`created_by` → `users`（删除后置空）。卸载成功后删除 |
| `agent_ssh_jobs` 改 | `instance_id` 可空；CHECK `kind` ∈ `prepare` / `install` / `uninstall` | 准备服务器的任务没有实例；部分唯一索引 `(target_id) WHERE status = 'running'` 不变，即同一台服务器同时只有一个 SSH 任务 |
| `agent_ops_actions` 改 | 新增 `server_id`；`instance_id` 可空；CHECK `action` 增加 `containers` | `server_id` → `agent_servers`（级联删除）；CHECK `instance_scope`：服务器级动作（`prune`、`containers`）的 `instance_id` 为空，其余必须有实例；部分唯一索引改为 `(server_id) WHERE status = 'running'`（同一台服务器同时只有一个进行中的动作）；索引 `(server_id, started_at)` |

**downgrade** 回到 0045 的结构，同样不保留这些记录：删除 `agent_deployments`，SSH 目标、任务与运维记录清空后改回按实例的列。

### 迁移 `0047`（T-0149）

移除 n8n 工作流与应用登录账号（`docs/decisions/2026-11-07-remove-n8n.md`）。这是删除数据的收缩迁移，升级前必须保存工作台数据库备份。

| 对象 | 升级行为 |
|---|---|
| `automation_runs`、`automations`、`automation_connections` | 删除表及其记录，包括执行历史、白名单、连接和加密的 API Key / 触发密钥 |
| `app_logins` | 删除表及其账号和加密密码；T-0082 后该表只允许 n8n |
| `idempotency_requests` | 只删除操作为 `automation.trigger` 的幂等记录；保留其他操作 |

**downgrade** 按旧结构恢复上述四张空表（`app_logins` 仍只允许 `n8n`），不恢复旧连接、凭据、工作流记录或触发幂等记录。要恢复移除前的数据，必须使用升级前的备份。历史迁移 `0010`、`0019`、`0020` 保持原样，仍可从空库走完整迁移链。

该迁移只影响工作台自己的数据库，不修改独立 n8n 服务的数据卷或工作流。旧通知与审计证据按各自的留存规则保留。

### 迁移 `0048`（T-0153）

新闻早报（`docs/v2/NEWS_PLAN.md` 第 9 节）。只新增，不改已有数据。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `news_settings` | `workspace_id`（主键，级联删除）、`enabled`、`push_times`（JSONB，`HH:MM`）、`briefing_mode`（CHECK `trendradar` / `agent`）、`agent_instance_id`（置空删除）、`platforms`、`newsnow_api_url`、`feeds`、`topics`、`exclusions`（JSONB）、`filter_method`（CHECK `keyword` / `ai`）、`ai_model`、`ai_api_base`、`ai_key_ref`、`ai_analysis`、`ai_translation`、`channels`（JSONB，明文设置）、`channel_secrets_ref`、`ingest_token_ref`、`config_rev`、`rendered_at`、`render_error`、`learned`（JSONB）、`updated_by`、`version`、时间戳 | 部分唯一索引 `uq_news_settings_enabled (enabled) WHERE enabled`：整个部署同时只有一个工作空间启用。没有时区列：时区由部署设置（`NEWS_TIMEZONE` = `.env` 的 `TRENDRADAR_TZ`）。三个 `*_ref` 是 `enc:v1:` 密文引用（用途 `news_settings:<工作空间>:<ai_key / channels / ingest>`），纳入密钥轮换 |
| `news_briefings` | `id`、`workspace_id`（级联删除）、`local_date`、`slot`、`source`（CHECK `trendradar` / `agent`）、`title`、`content`、`parts`、`total_parts`、`status`（CHECK `receiving` / `complete` / `pending` / `failed`）、`task_id`（置空删除）、`error`、时间戳 | 索引 `(workspace_id, created_at)`；部分唯一索引 `(task_id) WHERE task_id IS NOT NULL` |
| `news_feedback` | `id`、`workspace_id`、`user_id`（都级联删除）、`kind`（CHECK `favorite` / `dismiss`）、`url`、`title`、`source_type`（CHECK `hotlist` / `rss`）、`source_id`、`source_name`、`topic`、`created_at` | 唯一 `(workspace_id, user_id, kind, url)`；索引 `(workspace_id, created_at)` |
| `agent_wakeups.source` | CHECK 增加 `news` | 方案 B 的唤醒 |

**downgrade** 删除 `news` 唤醒，CHECK 改回，删除三张表。

### 迁移 `0049`（T-0159）

插拔式模块的启用记录（`docs/modules/README.md`）。只新增，不改已有数据；没有记录时按模块清单的 `default_enabled`，
所以已有工作空间不需要回填（新闻早报默认启用，行为不变）。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `workspace_modules` | `workspace_id`（级联删除）+ `module_key`（主键，CHECK `^[a-z][a-z0-9_]{1,31}$`）、`enabled`、`updated_by`（置空删除）、`updated_at` | owner 在应用中心启用 / 停用时写一行；停用不删除模块自己的数据 |

**downgrade** 删除这张表（只丢失“停用了哪些模块”的记录）。

新闻早报的三张表（迁移 `0048`）属于模块 `news`，模型在 `services/api/app/modules/news/models.py`。

### 迁移 `0050`（T-0161）

侧栏布局（`docs/v2/MODULES_PLAN.md` 第 2 节）。只新增；没有记录时用默认侧栏。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `workspace_navigation` | `workspace_id`（主键，级联删除）、`layout`（JSONB：`sections`、`pages`、`placements`、`section_order`，接口层校验）、`version`、`updated_by`（置空删除）、`updated_at` | 自定义分组、页面挪分组、零代码页面（外部链接、站内页面、知识库笔记、仪表盘、嵌入页） |

**downgrade** 删除这张表（自定义的分组和零代码页面会丢失）。

### 迁移 `0051`（T-0163）

任务的“已完成待查看”（`docs/v2/HOME_PLAN.md` 第 2 节）。已有任务不回填（迁移前完成的按已查看处理）。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `tasks` | `review_pending`（布尔，默认 false） | 智能体用 `complete_task` 完成时置真；owner / admin 确认结果、打回或人改完成状态时清掉 |
| `ix_tasks_review_pending` | `workspace_id`，部分索引 `WHERE review_pending` | 首页按工作空间数待查看的任务 |
| `task_events.kind` | 增加 `reviewed` | 确认了结果 |

**downgrade** 删除 `reviewed` 动态、恢复原来的种类约束，删除这一列和索引。

### 迁移 `0052`（T-0163）

数据迁移：从没改过的默认首页（组件恰好是迁移 `0004` 的四个入门组件，配置与位置都没动过）换成首页效果图的五个组件
（`work_summary`、`task_progress`、`news_briefing`、`my_todos`、`calendar_agenda`），仪表盘版本 +1。改过的首页不动，
可以在页面上“恢复默认首页”。

**downgrade** 把仍是新默认布局的首页换回四个入门组件；改过的保持原样。

### 迁移 `0053`（T-0166）

任务的五种状态（`docs/decisions/2026-11-09-task-work-states.md`）：完成轮次、个人查看记录、明确阻塞。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `tasks` | `completion_seq`（整数，默认 0，`>= 0`） | 完成轮次：每次未完成→完成加 1。迁移前完成的任务为 0（对所有人都是已完成）；`review_pending` 为真的任务记为 1 |
| `tasks` | `blocked_at`、`blocked_reason`（≤ 2000 字）、`blocked_by`（用户，置空删除） | 明确阻塞；`blocked_at` 与 `blocked_reason` 成对出现（`ck_tasks_blocked_pair`）。完成时清空 |
| `ix_tasks_blocked` | `workspace_id`，部分索引 `WHERE blocked_at IS NOT NULL` | 数阻塞的任务 |
| `uq_tasks_workspace_id_id` | `(workspace_id, id)` 唯一 | 作 `task_result_reads` 复合外键的目标 |
| `task_result_reads` | `task_id` + `user_id`（主键）、`workspace_id`、`seen_completion_seq`（`>= 0`）、`viewed_at` | 每个成员看过的最大完成轮次。`(workspace_id, task_id)` → `tasks`、`(workspace_id, user_id)` → `workspace_members`，都级联删除：任务删除或成员离开时随之删除，不能跨工作空间 |
| `ix_task_result_reads_user` | `(workspace_id, user_id)` | 按人读查看记录 |
| `task_events.kind` | 增加 `blocked`、`unblocked` | 标记 / 解除阻塞（`note` 是原因） |
| 删除 | `tasks.review_pending` 与 `ix_tasks_review_pending` | 由完成轮次与个人查看记录取代（信息已转成 `completion_seq = 1`） |

**downgrade** 恢复 `review_pending`（已完成、轮次 > 0、没有任何人看过这一轮的为真）与它的索引；删除阻塞动态、`task_result_reads`、新列与约束。

### 迁移 `0054`（T-0178）

OpenClaw 服务器改用保存的加密登录密码（`docs/decisions/2026-11-09-agent-server-password-auth.md`）。加法迁移，不删除、不清空旧数据。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `agent_ssh_targets` | `login_password_ref`（文本，可空） | 登录密码的 `enc:v1:` 密文引用，加密用途 `agent_server_password:<id>`，已加入加密密钥轮换；任何接口都不返回。旧记录为 null（旧密码从未保存，要补录） |
| `agent_ssh_targets` | `credential_revision`（整数，默认 0，`>= 0`） | 设置 / 替换密码加 1（主密钥轮换不加）；后台任务按它判断密码是否被换过 |
| `agent_ssh_targets` | `last_connection_at`、`last_connection_reason` | 最近一次显式连接检测：时间与白名单原因（`connected` / `auth_failed` / `unreachable` / `host_key_changed` / `credential_unreadable`，CHECK） |
| `agent_ssh_targets` | `legacy_key_cleanup_state`（文本，默认 `not_needed`） | 旧专用公钥的退役：`not_needed` / `pending` / `done` / `manual_required`（CHECK）；有 `client_key_ref` 或 `client_public_key` 的旧记录回填 `pending`。旧私钥 / 公钥列保留，显式退役成功后才清空 |
| `agent_deployments` | `runtime_installed`（布尔，可空） | 部署成功 true、卸载容器保留目录 false；旧记录 null（未知），不凭 `installed_at` 推断 |

**downgrade** 删除这些列与约束，其余数据原样保留；已保存的密码密文随之丢失，已经退役的远端公钥、已清空的旧私钥引用不会恢复。

### 迁移 `0055`（T-0185）

容器服务器的容器读数（`docs/decisions/2026-11-09-runner-container-metrics.md`）。加法迁移，全部可为空，旧记录不回填。

| 表 / 对象 | 字段 | 说明 |
|---|---|---|
| `runner_environments` | `cpu_used_cores`（浮点，可空，`>= 0`） | runner-agent 用两次累计 CPU 时间差分出的最近一个窗口的平均核数，可以大于 1；不是配置的 `cpus` |
| `runner_environments` | `cpu_window_seconds`（浮点，可空，1–180） | 对应的采样窗口；与 `cpu_used_cores` 同时有值或同时为空（CHECK `cpu_pair`） |
| `runner_environments` | `cpu_reported_at`、`mem_reported_at`、`disk_reported_at`（带时区时间，可空） | 平台收到对应读数的服务端时间（与 `cpu_used_cores`、`mem_bytes`、`disk_bytes` 一一对应），不是 agent 的时钟；普通 poll 不刷新，观测不改 `last_used_at` / `version` |

`mem_bytes` 仍是 Docker `memory_stats.usage`（含页缓存），`disk_bytes` 仍是环境数据卷的占用（不含镜像与可写层）；CPU、内存读不到时连同时间清空，
数据卷读不到时保留上一次的读数与原时间（回收规则要用）。**downgrade** 只删这五列与约束，`mem_bytes` / `disk_bytes` 原样保留。

### 大模型会话桥模块（迁移 0066，T-0228）

| 表 | 列 | 说明 |
|---|---|---|
| `llm_bridge_bridges` | `id`、`workspace_id`（级联删除）、`name`、`base_url`、`token_ref`、`enabled`、时间戳 | 工作空间登记的会话桥。唯一 `(workspace_id, name)`。`token_ref` 是 `enc:v1:` 密文引用（用途 `llm_bridge:<工作空间>:<桥>:token`），纳入密钥轮换 |
| `llm_bridge_grants` | `instance_id`（主键，级联删除）、`workspace_id`（级联删除）、`enabled`、`can_write`、`can_account`、`can_takeover`、`platforms`（JSONB 列表，空 = 全部）、`updated_at` | 每个实例的“大模型会话权限”。没有这一行等于没有授权 |

会话、回合、事件、各平台的登录状态不在工作台的数据库里，在会话桥自己数据卷的 SQLite（`services/llm-bridge/bridge/store.py`）。
**downgrade** 删掉这两张表。
