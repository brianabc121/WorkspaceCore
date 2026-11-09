# Model Connections Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. If the human selects parallel execution, use superpowers:subagent-driven-development. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 交付连接、模型、检查、授权与旧凭据迁移的后端能力。  
**Architecture:** 新业务元数据引用原 AgentSecret，所有取用路径汇入统一校验；管理接口与实例配置接口分离。  
**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL、Redis/arq、httpx。  
**Spec:** ../specs/2026-11-09-model-connections-design.md 与 ../specs/2026-11-09-model-connections-contract.md。

## Global Constraints

- 基线4fbf863仅作定位，开工以最新origin/main为准；遵守AGENTS和当前Sprint批准。
- 不手写schema.d.ts；后端导出OpenAPI后由生成器生成。
- owner + workspace隔离；新表复合外键包含workspace。
- 不回显Key；密文context保持secret_id/value_version，不复制后换ID。
- 不破坏Runner与旧alias；不跨任务修改新闻、邮件、GitHub凭据。
- 新接口以契约为准；未核验上游协议不开启。
- 以下新增文件名为建议，已有同责模块优先复用，任务日志记录实际paths。

## Review Focus

1. 旧api_token可能是非模型凭据：MC-3必须证明不被误启用、不影响Runner。
2. 老检测回包覆盖新Key：MC-2必须有修订竞态测试。
3. base URL混合DNS/重定向把Key带去别处：MC-2必须有网络策略测试。
4. 解绑/停用只影响后续取用：MC-1/MC-3必须测试旧resolver和Runner旁路。
5. 同一迁移反复执行/并行执行：MC-3必须证明一对一、可恢复且不重复授权。

## 文件地图

新增建议：
- services/api/app/models/model_connection.py：连接、模型目录、授权、检查与绑定模型。
- services/api/app/schemas/model_connections.py：管理/实例资源契约。
- services/api/app/domain/model_connections.py：CRUD、授权、轮换入口。
- services/api/app/domain/model_catalog.py：模型快照与同步。
- services/api/app/integrations/model_providers.py：经核验的协议适配、元数据/推理检查。
- services/api/app/api/v1/model_connections.py：owner API。
- services/api/app/commands/migrate_model_connections.py：迁移CLI。
- services/api/tests/test_model_connections_api.py、test_model_connection_checks.py、test_model_connection_migration.py。
修改：app/models/__init__.py、app/main.py或实际router注册处、api/deps.py、domain/agents.py、domain/runner_hosts.py、domain/credential_rotation.py（仅若存储结构改变）、migrations/versions/<新revision>.py、openapi.json。

## MC-0：核验协议与冻结接入契约

**Files:** 读取agent_bundle、agent_provisioning、integrations/http.py、现有ADR；新增docs/decisions/2026-11-09-model-connections-provider-verification.md。
**Consumes:** 本包契约和仓库锁定镜像。
**Produces:** 协议能力表，记录配置枚举、URL拼接、鉴权方式、模型列表/分页、推理路径、SecretRef支持、应用方式与真实证据；提供supported_protocols列表。

- [ ] 阅读锁定OpenClaw源码/schema或运行实例，核验其实际models.providers/主模型/SecretRef字段，不用概念文档猜。
- [ ] 分别核验OpenAI官方、一个OpenAI兼容中转；原生Anthropic/Gemini列入核验表，不能从品牌名推断协议。
- [ ] 写test_provider_capabilities_hide_unverified_protocols：未核验协议不出现在可选项，返回明确unsupported。
- [ ] 用可注入假HTTP transport写URL拼接/鉴权测试；证明不会重复/v1，Key不进URL。
- [ ] 记录官方来源、版本/commit、日期和真实命令输出；无法核验的范围显式blocked，不能编造验证通过。
- [ ] 提交ADR与能力表，再推进MC-1/MC-2。

## MC-1：连接资源、内部凭据与授权

**Files:** models/model_connection.py、schemas/model_connections.py、domain/model_connections.py、api/v1/model_connections.py、注册/依赖、迁移、新API测试。
**Consumes:** MC-0的协议能力；现有CredentialBox、AgentService加密/轮换。
**Produces:** ModelConnectionService.list/get/create/update/rotate_key/set_grants/delete；契约第2节CRUD/summary/grants。
建议签名：
- create(access, body: ModelConnectionCreate, *, client_ip: str) -> ModelConnectionOut
- rotate_key(access, connection_id: UUID, body: ModelConnectionKeyRotate, *, client_ip: str) -> ModelConnectionOut
- authorize_model_credential(db, *, workspace_id: UUID, instance_id: UUID, connection_id: UUID) -> AuthorizedModelCredential（内部类型，不序列化Key）\n- check_managed_secret_access(db, *, workspace_id: UUID, instance_id: UUID, secret_id: UUID) -> None（旧resolver/Runner共享的停用/撤权检查；分类不清理治理映射）

- [ ] 写test_owner_workspace_isolation：owner成功，admin/member失败；跨workspace id/instance grant为404且无写入。
- [ ] 写test_connection_crud_never_returns_key：create/list/detail/update/activity均无明文/密文；secret只在内部加密列。
- [ ] 写test_imported_draft_with_legacy_grants_can_complete_endpoint：保留旧授权时首次补齐不报409、不变更旧运行时；已配置目标的后续变更仍受限。\n- [ ] 写test_used_endpoint_is_immutable、test_delete_used_connection_conflicts、test_disabled_connection_denies_credential：409影响信息正确、停用不删除实例。
- [ ] 写test_version_conflict_and_idempotent_create：旧version不覆盖；重复Idempotency-Key不重复建secret。
- [ ] 运行上述测试，保存真实失败输出；实现数据约束和最小事务服务。
- [ ] 连接更新/旧secret写入复用同一业务校验；不要通过老rotate接口绕过version/托管关系。
- [ ] 测试空库upgrade、现有库upgrade、迁移约束/索引；迁移编号在最新head上生成。
- [ ] 运行新API测试与test_agents_api.py、test_credential_rotation.py；通过后导出OpenAPI，提交。

## MC-2：多模型目录与真实连接检查

**Files:** domain/model_catalog.py、integrations/model_providers.py、schemas/routers、新check测试；复用integrations/http.py。
**Consumes:** Connection + config_revision、MC-0协议能力。
**Produces:** sync_models(access,id,version)；check_connection(access,id,body)；完整快照和ConnectionCheckOut。

- [ ] 写test_manual_and_all_available_catalog：一个Key多模型；不支持模型列表时保留手工模式，all_available拒绝假空目录。
- [ ] 写test_partial_sync_preserves_snapshot：分页失败/超1000条/响应过大不覆盖旧完整集合；被绑定模型不能默默消失。
- [ ] 写test_check_status_and_retry_at：覆盖200元数据、401、403、429秒数/日期、5xx、超时、模型不存在和非JSON；元数据不等同推理。
- [ ] 写test_stale_check_does_not_replace_new_revision：请求在途时轮换Key，旧回包不更新last_check。
- [ ] 写test_outbound_policy_prevents_secret_exfiltration：私网/metadata/混合DNS/重绑定/跳转被拒绝，TLS保持校验。
- [ ] 实现有界分页、请求大小、限流和脱敏；推理POST不自动重试，旧结果保留且标旧。
- [ ] 测试all_available只有明确同步ID，只有真实探测过的模型标verified；不能按品牌chips写模型ID。
- [ ] 运行check/API/HTTP client相关测试，通过后更新OpenAPI并提交。

## MC-3：幂等导入与共享凭据兼容

**Files:** commands/migrate_model_connections.py、domain/model_connections.py、domain/agents.py、domain/runner_hosts.py、迁移/回归测试；迁移操作手册。
**Consumes:** 原AgentSecret/Grant/Access；MC-1映射和授权；迁移手册。
**Produces:** migrate_workspace(db, workspace_id: UUID, *, dry_run: bool, batch_size: int=200) -> MigrationReport；每个旧secret最多一个受管/待归类记录。

- [ ] 写test_migration_preserves_ids_versions_grants_and_accesses：旧ID、value_version、alias、记录不变，密文仍能用原context解密。
- [ ] 写test_migration_is_idempotent_concurrent_and_resumable：重复/竞争插入/中途事务失败后继续，无重复连接、授权或假完成。
- [ ] 写test_classify_non_model_cannot_restore_revoked_access：先停用/撤权，再分类为non_model，旧resolver/Runner仍拒绝，映射不删除；只有owner显式重新启用/授权才恢复。\n- [ ] 写test_classification_roundtrip_preserves_identity_and_governance：model↔non_model不新建secret/连接，保留旧授权；有原生绑定时拒绝改判non_model，兼容接口不能绕过停用/版本。\n- [ ] 写test_api_token_is_not_automatically_openai：model_api_key→待完善；api_token/generic→待归类；缺元数据不能产生新模型绑定；保留旧路径取用的 enabled 语义见迁移手册。
- [ ] 写test_legacy_and_runner_follow_managed_policy：托管连接停用/授权撤销后，旧resolver和Runner均无法绕过；非模型不退化。
- [ ] 写test_master_key_rotation_keeps_imported_credentials_readable：引用原secret时现有轮换覆盖；若改变存储，先扩展轮换FIELDS及测试。
- [ ] 实现只读dry-run、按workspace分批、唯一约束、脱敏结果；不要在Alembic里访问远端服务。
- [ ] 处理旧接口写操作的委托和元数据版本；新的原生连接不能在旧密钥列表再次可编辑。
- [ ] 执行测试，记录真实输出；对已有引用只做扩展迁移，不drop旧表/字段，提交。

## 后端阶段验收命令

从repo根目录：
~~~bash
make lint-api
cd services/api
uv run pytest tests/test_model_connections_api.py tests/test_model_connection_checks.py tests/test_model_connection_migration.py
uv run pytest tests/test_agents_api.py tests/test_runner_api.py tests/test_credential_rotation.py
uv run alembic heads
uv run alembic upgrade head
cd ../..
make openapi
python3 scripts/collab.py check
~~~

这些是未来实施命令，非本包已运行结果。集成用例必须配置专用TEST_DATABASE_URL/TEST_REDIS_URL；大量SKIP不算数据库迁移/权限已验收。
