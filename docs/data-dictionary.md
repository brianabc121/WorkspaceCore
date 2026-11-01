# 数据字典

> 状态：Sprint 6（迁移到 `0017`）。目标实体清单见 `docs/DEVELOPMENT_SPEC.md` 第 5 节（V2 见 `docs/v2/TRANSFORMATION_PLAN.md` 第 5 节）；本文记录**已经迁移**的数据库对象。
> 迁移 `0001` ~ `0017` 都只新增对象，都有 downgrade；`0011` 起没有改动 `0010` 及以前的表，唯一的加列是 `0014` 给 `0013` 新建的 `im_messages` 加可空列 `idempotency_key`（无默认值）。升级与回退的做法见 `docs/deployment.md` 第 5 节。

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
| `integrations` | `id`、`workspace_id`、`provider`、`name`、`base_url`、`public_url`、`status`、`settings_json`、`webhook_secret_ref`、`version`、`created_at`、`updated_at` | 随工作空间级联删除；`(workspace_id, provider, name)` 唯一，V1 每个工作空间每个 provider 最多 1 个由服务层保证；`status` ∈ `pending` / `active` / `unavailable` / `unauthorized` / `disabled`；`settings_json` 只放非敏感元数据（`tested_version`、`instance_version`、`last_tested_at`、Webhook 注册状态）；`webhook_secret_ref` 是加密后的签名密钥引用；`version` 用于乐观并发 |
| `integration_accounts` | `id`、`integration_id`、`user_id`、`auth_type`、`credential_ref`、`scopes_json`、`expires_at`、`status`、`created_at`、`updated_at` | `integration_id` 唯一（V1 一个集成一个凭证），随集成级联删除；`user_id` → `users`（删除后置空）；`credential_ref` 只存 `enc:v1:` 加密引用（AES-256-GCM，附加数据绑定本行 ID），明文 Token 从不入库 |
| `external_resources` | `id`、`workspace_id`、`integration_id`、`resource_type`、`external_id`、`title`、`source_url`、`metadata_json`、`source_updated_at`、`synced_at`、`deleted_at` | 外部项目与任务的本地投影（只读缓存，Vikunja 是主源）；`(integration_id, resource_type, external_id)` 唯一；外部的更新时间比已存的旧时不覆盖（乱序事件）；外部删除后记 `deleted_at`（tombstone），查询一律排除；部分索引 `ix_external_resources_workspace_type`（`deleted_at IS NULL`）；任务的展示字段（完成、优先级、截止时间、标签、version 等）在 `metadata_json` 里 |
| `idempotency_requests` | `id`、`workspace_id`、`actor_id`、`idempotency_key`、`request_fingerprint`、`status`、`response_code`、`response_json`、`context_json`、`created_at`、`updated_at`、`expires_at` | `(actor_id, idempotency_key)` 唯一；随工作空间或用户级联删除；`status` ∈ `processing` / `succeeded` / `failed` / `unknown`；`response_json` 保存重放用的响应（任务 DTO 或错误码，不含 Token 与外部响应原文）；保留 24 小时，`expires_at` 有索引供清理 |
| `sync_cursors` | `id`、`integration_id`、`resource_type`、`cursor_value`、`last_success_at`、`last_error`、`updated_at` | `(integration_id, resource_type)` 唯一，随集成级联删除；`last_success_at` 决定 `sync.status`（超过 `SYNC_STALE_AFTER_SECONDS` 为 stale）；`last_error` 只存错误分类，不存外部响应 |

规则见 Sprint 3 契约 ADR 第 4、7、10 节；全部是新表，回退即删除。

### 迁移 `0006`（S3-05）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `webhook_inbox` | `id`、`integration_id`、`event_key`、`event_type`、`payload_json`、`status`、`attempts`、`next_retry_at`、`received_at`、`processed_at`、`error_text` | 签名校验通过后才写入；`(integration_id, event_key)` 唯一，`event_key = sha256(原始请求体)`，重复送达只记录一次；随集成级联删除；`status` ∈ `pending` / `processing` / `completed` / `retry` / `dead_letter`；`payload_json` 只有事件名、时间与任务 / 项目 ID（不保存 `doer` 等内容）；`next_retry_at` 是下次可领取的时间，处理中时是租约到期时间；部分索引 `ix_webhook_pending`（`status IN ('pending','retry','processing')`）；`error_text` 只存错误分类；处理完超过 7 天的事件由 Worker 清理，`dead_letter` 保留 |

`sync_cursors` 在 0005 已建立；T-0021 增加 `resource_type = full_sync` 一行，记录最近一次全量同步的时间。规则见 Sprint 3 契约 ADR 第 9、10.1 节。

### 迁移 `0007`（S4-01、S4-02）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `knowledge_sources` | `id`、`workspace_id`、`provider`、`name`、`base_url`、`version`、`created_at`、`updated_at` | 工作空间配置的 AFFiNE 外部地址（`provider = affine`），工作台从不访问它；随工作空间级联删除；V1 每个工作空间最多 1 个，由服务层加锁计数保证；`version` 用于乐观并发 |
| `bookmarks` | `id`、`workspace_id`、`source_id`、`title`、`note`、`link_kind`、`affine_workspace_id`、`doc_id`、`suffix`、`visibility`、`owner_id`、`version`、`created_at`、`updated_at` | AFFiNE 文档收藏，**不含文档正文**；随知识来源或工作空间级联删除；`suffix` 是来源地址之后的路径与保留的查询参数，完整链接 = 来源的 `base_url` + `suffix`；`visibility` ∈ `workspace` / `private`，`link_kind` ∈ `workspace_doc` / `share`（CHECK 约束）；`owner_id` → `users`（删除后置空，私有收藏随之对所有人不可见）；部分唯一索引 `uq_bookmarks_workspace_doc`（工作空间可见的每篇文档一条）与 `uq_bookmarks_private_doc`（每个用户的私有收藏每篇文档一条）；索引 `(workspace_id, updated_at)` |
| `resource_links` | `id`、`workspace_id`、`source_resource_id`、`target_resource_id`、`relation`、`created_at` | 跨系统关联（规范 5.1 第 11 项）：`source_resource_id` → `bookmarks`、`target_resource_id` → `external_resources`（Vikunja 项目 / 任务投影），任一方删除时级联删除；目标写了 tombstone（`deleted_at`）时读取一律排除，页面不留僵尸引用（A10）；`(source_resource_id, target_resource_id)` 唯一；`relation` 目前只有 `related` |
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
- `sync_cursors.resource_type = github`：增量同步的游标（上次同步开始时间往前 2 分钟）；全量同步的时间与 Vikunja 一样记在 `full_sync`。

### 迁移 `0008`（S4-03、S4-05）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `search_documents` | `id`、`workspace_id`、`resource_type`、`resource_id`、`provider`、`title`、`body`、`context`、`source_url`、`owner_id`、`visibility_json`、`source_updated_at`、`search_vector`、`indexed_at` | 跨资源搜索索引（规范 5.1 第 12 项），**只由触发器维护**，应用只读。触发器 `search_index` 挂在 `external_resources`、`bookmarks`、`calendar_events` 上，插入、更新、tombstone、删除都在同一个事务里同步；Actions 运行不进索引。只存标题、备注、说明、地点、标签与上下文（项目名 / 仓库名 / 日期），不含 Token 与外部文档正文。`(resource_type, resource_id)` 唯一；随工作空间级联删除；`owner_id` 只在私有收藏上有值（查询时只对该用户可见），创建者被删除的私有收藏从索引移除；`search_vector` 是 `to_tsvector('english', title \|\| ' ' \|\| body)` 的生成列（GIN 索引）；`title` 另有 `gin_trgm_ops` 索引供中文模糊匹配；收藏的 `source_url` 为 NULL，查询时用知识来源的地址拼出。`workspace.search_rebuild()` 整体重建（`python -m app.commands.reindex_search`） |

### 迁移 `0009`（S5-03）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `notifications` | `id`、`workspace_id`、`user_id`、`type`、`severity`、`title`、`body`、`link`、`source_key`、`read_at`、`created_at` | 通知中心，每个收件人一行。`(user_id, workspace_id, source_key)` 在 `source_key` 非空时唯一，同一事件不会重复通知；`ix_notifications_unread` 是只含未读的部分索引（未读数）；`ix_notifications_recipient_created` 用于按时间分页；随用户、工作空间级联删除。`title` / `body` 按纯文本显示，不含 Token 与第三方 payload |

### 迁移 `0010`（S5-04）

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
- `integrations.webhook_secret_ref`；
- `automation_connections.api_key_ref`、`trigger_secret_ref`；
- `agent_secret_versions.ciphertext_ref`；
- `agent_ca.key_ref`。

步骤见 `docs/deployment.md` 第 6.1 节。

### 迁移 `0012`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_accounts` | `user_id`、`im_user_id`、`created_at` | 管理员（owner / admin）的 OpenIM 账号，一个用户一个，多个工作空间共用，第一次需要时创建；`user_id` 即主键，随用户级联删除；`im_user_id`（`ws_` + 用户 UUID 十六进制）唯一；不保存 IM 令牌（imAdmin 令牌缓存在 Redis）。实例的账号在 `agent_instances.im_user_id` |
| `im_groups` | `id`、`workspace_id`、`group_id`、`name`、`announcement`、`owner_user_id`、`relay_enabled`、`max_agent_streak`、`max_per_minute`、`dismissed_at`、`version`、`created_at`、`updated_at` | 工作空间的群，随工作空间级联删除；`group_id` 是 OpenIM 群 ID（`ws` + 32 位十六进制，由建群的 Idempotency-Key 推出；会话 ID 为 `sg_<group_id>`），唯一；`owner_user_id` 是群主 → `users`（删除后置空）。防刷屏阈值直接放在群上（方案的 `im_group_policies` 合并进来），计数在 Redis：CHECK `max_agent_streak` 在 1–50 之间（默认 8：管理员上次发言后，实例最多连续几条），`max_per_minute` 在 1–60 之间（默认 6：单个实例每分钟几条）。`relay_enabled = false` 时，拒收实例 @ 其他实例的群消息；解散只记 `dismissed_at`，不删行；`version` 用于防刷屏设置的乐观并发 |
| `im_group_members` | `id`、`group_pk`、`im_user_id`、`instance_id`、`user_id`、`role`、`joined_at` | `group_pk` → `im_groups`、`instance_id` → `agent_instances`、`user_id` → `users`，都级联删除；CHECK `one_member`：`instance_id` 与 `user_id` 恰好一个有值（系统账号 `ws_notice` 进群但不记在这里）；`role` ∈ `owner` / `admin` / `member`；`(group_pk, im_user_id)` 唯一；索引 `ix_im_group_members_im_user_id`；成员只能是本工作空间的实例与 owner / admin，由服务层检查 |
| `im_webhook_inbox` | `id`、`event_key`、`command`、`payload_json`、`status`、`attempts`、`next_retry_at`、`received_at`、`processed_at`、`error_text` | OpenIM after 回调（发消息、上下线、删好友）的收件箱。回调没有签名，回调地址带口令，只接受来自 OpenIM 网段的请求；before 回调同步判断，不进收件箱。`event_key` 唯一（`msg:<serverMsgID>`、`online:` / `offline:<用户>:<平台>:<seq>`、`delete_friend:<owner>:<friend>:<收到的毫秒>`），重复送达只记录一次。`payload_json` 是回调原始请求体（上限 256 KiB），处理时只取其中的 ID，内容以 `pull_msg_by_seq` 拉回的为准。不关联工作空间，没有外键。`status` ∈ `pending` / `processing` / `retry` / `completed` / `dead_letter`，失败 8 次进 `dead_letter`；`next_retry_at` 是下次可领取的时间，处理中时是 2 分钟租约的到期时间；部分索引 `ix_im_webhook_pending`（`status IN ('pending','retry','processing')`）；`error_text` 只存错误分类。处理完超过 7 天的事件由 Worker 清理，`dead_letter` 保留 |

### 迁移 `0013`（S6-02，T-0055）

| 表 | 主要字段 | 约束与说明 |
|---|---|---|
| `im_conversations` | `id`、`workspace_id`、`owner_user_id`、`conversation_id`、`kind`、`peer_instance_id`、`group_pk`、`pinned_at`、`muted`、`read_seq`、`max_seq`、`last_message_at`、`version`、`created_at`、`updated_at` | 每个管理员在一个工作空间里的会话，已读、置顶、免打扰各自独立；`(owner_user_id, conversation_id)` 唯一，`conversation_id` 另有索引；`conversation_id` 是 OpenIM 会话 ID（单聊 `si_`、群聊 `sg_`、通知 `sn_`）；`kind` ∈ `single` / `group` / `notice`，`notice` 是本工作空间的通知账号 `wn_<工作空间>` 发审批卡片的会话；`workspace_id`、`owner_user_id`（→ `users`）、`peer_instance_id`（→ `agent_instances`）、`group_pk`（→ `im_groups`）都级联删除。CHECK：`read_seq`、`max_seq` ≥ 0，两者只增不减；未读数 = 序号大于 `read_seq`、且不是自己发的消息条数，`muted` 的会话不计入顶栏未读。`pinned_at` / `muted` 先同步到 OpenIM，失败时本地不改；`version` 在修改置顶 / 免打扰时递增，接口不校验 |
| `im_messages` | `id`、`workspace_id`、`conversation_id`、`server_msg_id`、`client_msg_id`、`seq`、`sender_im_id`、`sender_instance_id`、`content_type`、`content_json`、`plain_text`、`at_im_ids`、`status`、`sent_at`、`created_at` | 消息投影，每条 OpenIM 消息一行，按 OpenIM 会话 ID 只存一份：群里的多个管理员共用，不关联 `im_conversations` 的行。序号与内容以 `pull_msg_by_seq` 拉回的为准，每个会话最多补最近 1000 条。**保存消息内容**：`content_json` 是解码后的 content（超过 64 KiB 或无法解析时为 `{}`），`plain_text` 是文字消息正文或系统消息说明；图片消息只有地址与尺寸，图片本身在 OpenIM 对象存储。`server_msg_id` 唯一，`(conversation_id, seq)`、`(conversation_id, client_msg_id)` 各自唯一；工作台代发、还没被 OpenIM 受理的消息 `seq` 与 `server_msg_id` 为空。`status` ∈ `sending` / `sent` / `failed` / `unknown`；`sender_instance_id` → `agent_instances`（删除后置空）；随工作空间级联删除；索引 `(conversation_id, sent_at)` 与 `sent_at`。保留 `IM_MESSAGE_RETENTION_DAYS`（默认 365 天），超期由 Worker 按 `sent_at` 删除，OpenIM 里的消息不受影响 |

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

ORM 模型与迁移的一致性由集成测试中的 `alembic check` 保证。
