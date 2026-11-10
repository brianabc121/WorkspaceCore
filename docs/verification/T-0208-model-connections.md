# T-0208 中转站管理与 OpenClaw 主模型：核验记录

方案 `docs/v2/model-connections/`（README、设计、接口约定、三份实施计划、验收矩阵、迁移手册、效果图）。任务 T-0200 ~ T-0208。
决定与 OpenClaw 核验：`docs/decisions/2026-11-09-model-connections.md`。

报告里分清三种证据：**单元 / 模拟**（pytest、vitest 里的模拟服务商或模拟接口）、**真实 OpenClaw + 假上游**（锁定镜像 + 假模型服务）、
**真实服务商**（没有做：没有用户的真实 Key，不声称在真实 OpenAI / Anthropic / Google 上验证过）。

## 1. 环境

| 项目 | 值 |
|---|---|
| 基线 | main `bc74494` |
| OpenClaw | `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`（本机已缓存） |
| Python / Node | 3.12.3（services/api 的 venv）/ 本机 22.22.0（CI 的 web 任务用 Node 22，agents 任务的浏览器验收用 22.23.4） |
| 数据库 | 本会话自己的 PostgreSQL 16 与 Redis 7 容器（`TEST_DATABASE_URL`、`TEST_REDIS_URL`、`REQUIRE_INTEGRATION=1`；不写密码） |

## 2. OpenClaw 核验（MC-0，真实 OpenClaw）

在 `--network none` 的容器里运行锁定镜像的 CLI 与网关，用容器内的抓包服务记录 OpenClaw 发出的每个请求（假 Key）。
结论写在 ADR 第 2 节；原始输出在本会话的临时目录（核验时的 `oc-verify/out/`：`key-excerpts.md`、`validate-*.txt`、`capture/summary.txt`、
`live/gateway-reload-log.txt`、`config-schema.json`），没有提交进仓库（含镜像代码摘录）。要点：

- `api` 取值 12 个；`openai-completions` → `POST <baseUrl>/chat/completions` + `Authorization: Bearer`；
  `anthropic-messages` → `/v1/messages` + `x-api-key` + `anthropic-version: 2023-06-01`；`google-generative-ai` → `/models/<id>:streamGenerateContent?alt=sse` + `x-goog-api-key`；
- `models.providers.*` 与 `agents.defaults.model` 热加载（约 0.2 秒），无效配置跳过、保留上一份；同一 SecretRef 换值要 `secrets reload`；
- `config patch --stdin [--dry-run --allow-exec --json]` 一次校验写入；失败时 `checks` 仍为 true，类别在 `errors[].kind`（本次实测补充）；
- provider 键要小写、不能含 `/`；`models[]` 每项要 `id` 与 `name`；入口的 `doctor` 会改写配置文件，只能打补丁不能整份覆盖。

## 3. 本地结果

交付分支 `claude/cool-wright-99qbbg`。数据库测试都连真实的 PostgreSQL 16 / Redis 7（`REQUIRE_INTEGRATION=1`，没有跳过数据库测试）。

| 检查 | 命令（执行目录） | 结果 |
|---|---|---|
| 后端静态检查 | `ruff check app tests`、`ruff format --check app tests`、`mypy app tests`（services/api） | 全部通过（mypy：397 个文件无问题） |
| 后端全量 | `pytest -q`（services/api） | 1316 passed、15 skipped，退出码 0（44 分 55 秒）。跳过的 15 个都要外部服务：真实 GitHub 3、真实 OpenIM 7、Garage S3 5（跳过原因都是没有设置对应的测试环境变量）；`model-config.mjs` 等脚本测试都实际运行。之后新增的 `test_switching_one_instance_keeps_the_shared_legacy_key` 单独运行通过 |
| 中转站相关后端 | `pytest tests/test_model_connections_api.py tests/test_model_connection_checks.py tests/test_model_connection_migration.py tests/test_agent_model_bindings.py tests/test_model_config_script.py` | 包含在全量里；单独运行同样通过 |
| 数据库迁移 | `alembic upgrade head`（空库与 0055 → 0056）、`alembic downgrade 0055` 后再升级 | 通过；降级只删新表，旧密钥库与旧授权原样保留（见第 3.2 节） |
| OpenAPI 与前端类型 | `python scripts/export_openapi.py --check`、`npm run check:api`（apps/web） | 一致 |
| 前端 | `npm run lint`、`npm run typecheck`、`npx vitest run`、`npm run build`（apps/web） | lint / typecheck 无输出；`vitest run` 76 个测试文件 744 个测试全部通过（之后新增键盘焦点与版本冲突两项，所在文件 12 + 8 个测试通过）；构建成功 |
| 浏览器（本机，真实后端） | 把 `tests/e2e/sprint6_browser.cjs` 里新加的两段原样取出，对本机 API + vite 执行 | 通过：旧 `/agents/secrets` 跳到“迁移与兼容凭据”、添加非模型凭据；添加第三方中转（Key 只写不读）、授权给实例、在“OpenClaw 连接分配”设主模型，状态“待应用” |
| 真实 OpenClaw（本机） | `tests/e2e/model_config_openclaw.py`（锁定镜像 + `fake_workspace_agent_api.py` + 假模型服务） | 15/15：config patch 校验写入、热加载、`secrets reload`、失败分类（`errors[].kind`）、局部合并、撤销 |
| 接入包配置校验 | 用锁定镜像的 `openclaw config validate` 校验接入包生成的 `openclaw.json`（受管 provider / 旧模板 / 无模型三种） | 通过 |
| 原生协议 | `docs/verification/T-0202-native-protocols.md` | Anthropic Messages、Gemini 各在真实 OpenClaw 上跑通一轮 |

### 3.1 证据类型

- **单元 / 模拟**：`test_model_connection_checks.py`（模拟服务商响应：请求格式、分页、错误归类、429、超时、TLS、不跟随跳转）、
  `test_model_config_script.py`（用假的 `openclaw` 命令跑 `model-config.mjs` 的补丁与失败分类）、vitest（模拟接口）。
- **真实数据库集成**：`test_model_connections_api.py`、`test_model_connection_migration.py`、`test_agent_model_bindings.py` 等，经真实 FastAPI 应用与 PostgreSQL。
- **真实 OpenClaw + 假上游**：本机 `model_config_openclaw.py`；CI 的 agents 场景 27（整套工作台 + 两个真实 OpenClaw 实例 + 假模型服务，见第 4 节）。
- **真实服务商**：没有做。没有用户的真实 Key，不声称在真实 OpenAI / Anthropic / Google 上验证过；官方地址与请求格式依据见 ADR 第 2、3 节。

### 3.2 迁移演练（脱敏）

演练库只在本会话的 PostgreSQL 里，演练后已删除；Key 是随机生成的假值，下面只有计数。

1. 用 main（`bc74494`，迁移 0055，没有中转站管理）的代码建工作空间、两个实例（甲、乙）、四个旧密钥
   （`model_api_key` ×2、`api_token` ×1、`generic` ×1），给甲授权 `openai_api_key` 与 `github_token`，给乙授权 `openai_api_key`（共 3 条旧授权）；
2. 换成本分支代码：`alembic upgrade head`（0055 → 0056）；
3. `python -m app.commands.migrate_model_connections --workspace-id <演练空间>`：

| 模式 | 退出码 | 扫描 | 新建 | 已映射 | 待完善 | 待归类 | 不可解密 | 失败 | 批次状态 |
|---|---|---|---|---|---|---|---|---|---|
| `--dry-run` | 0 | 4 | 4（将新建） | 0 | 2 | 2 | 0 | 0 | read_only |
| `--apply` | 0 | 4 | 4 | 0 | 2 | 2 | 0 | 0 | committed |
| `--apply`（重复） | 0 | 4 | 0 | 4 | 2 | 2 | 0 | 0 | committed |
| `--report` | 0 | 4 | 0 | 4 | 2 | 2 | 0 | 0 | read_only |

`--report` 的对账：未导入 0；`model_api_key` 2 项都是“待完善”，`api_token` / `generic` 各 1 项“待归类”；旧授权 3 条 → 连接授权 3 条，
缺少 0、多出 0；绑定 0（迁移不建绑定）；取用记录 0。迁移后 `agent_secrets` 仍是 4 行、`agent_secret_versions` 仍是 4 行（没有复制密文），
四份输出里没有 Key 或密文（`grep 'sk-rehearsal\|enc:'` 为 0）。

4. `alembic downgrade 0055`：新表删除，`agent_secrets` 4 行、`agent_secret_grants` 3 行原样保留；再 `upgrade head` 通过。

页面上的同一操作（“迁移记录”里的预览导入 / 导入旧密钥 / 对账）调用 `POST /api/v1/model-connections/migration/run`，
由 `test_owner_runs_the_import_from_the_page` 覆盖（只有 owner、要 CSRF、输出只有计数）。

## 4. CI 结果

CI_RESULTS

## 5. 验收矩阵

证据类型：U = 单元 / 模拟，D = 真实数据库集成，O = 真实 OpenClaw + 假上游（CI agents 场景 27 或本机 `model_config_openclaw.py`），
B = 浏览器（`sprint6_browser.cjs`，CI 与本机），V = vitest。没有一项用真实服务商（见 3.1）。后端测试名省略 `tests/` 前缀。

### 5.1 产品与 API

| ID | 证据 | 类型 |
|---|---|---|
| A01 | `test_model_connections_api.py::test_native_official_presets`、`test_official_preset_must_stay_on_official_host`；`test_model_connection_checks.py::test_list_models_request_shape_and_complete_listing`（`Authorization: Bearer`、`/models`）；场景 27 按协议发到连接的地址 | D、U、O（官方主机本身没有实测，见 3.1） |
| A02 | 场景 27：甲、乙用同一条连接 A 的不同模型，按请求里的 `model` 区分；`test_models_replace_and_in_use_models` | O、D |
| A03 | `test_model_connection_checks.py::test_manual_and_all_available_catalog`、`test_truncated_listings_pass_metadata_but_are_partial` | D、U |
| A04 | `test_unsupported_models_endpoint_keeps_manual_mode`、`test_partial_sync_preserves_snapshot_and_bound_models`；vitest `test_statuses_do_not_collapse_to_empty` | D、V |
| A05 | `test_list_filters_total_and_summary`、`test_connection_crud_never_returns_key`、`test_list_shows_first_three_models_in_one_response`；vitest `test_connections_list_filters_total_and_summary` | D、V |
| A06 | `test_metadata_status_classification`、`test_inference_status_classification_never_retries`、`test_rate_limit_parses_retry_after_and_is_not_retried`、`test_parse_retry_after_only_accepts_legit_future_values`、`test_total_deadline_bounds_slow_servers`、`test_production_transport_keeps_tls_verification_and_no_redirects`；vitest `test_statuses_do_not_collapse_to_empty` | U、V |
| A07 | `test_check_results_are_categories_and_metadata_is_not_inference`；页面汇总写明“元数据检测不代表模型能推理” | D、V |
| A08 | `test_delete_used_connection_conflicts`、`test_models_replace_and_in_use_models`、`test_revoking_the_grant_needs_the_binding_moved_first` | D |
| A09 | `test_used_endpoint_is_immutable`；vitest `test_official_relay_and_multi_model_form`（引导复制为新连接） | D、V |
| A10 | `test_rotate_key_bumps_versions_and_destroys_old_ciphertext`、`test_key_rotation_reapplies_bound_instances`；场景 27 第 3 步（换 Key 后下一轮用新 Key、旧 Key 不再使用） | D、O |
| A11 | `test_adapter_for_maps_every_protocol`、`test_native_protocols_are_open_for_relays`（只开放核验过的三个协议，其他 422） | U、D |

### 5.2 迁移与兼容

| ID | 证据 | 类型 |
|---|---|---|
| M01 | `test_migration_preserves_ids_versions_grants_and_accesses`、`test_imported_draft_with_legacy_grants_can_complete_endpoint`；迁移演练（3.2：model_api_key 是待完善、绑定 0） | D |
| M02 | `test_api_token_is_not_automatically_openai`；演练（api_token / generic 是待归类） | D |
| M03 | `test_migration_is_idempotent_concurrent_and_resumable`、`test_insert_conflicts_are_resolved_without_errors`；演练里重复 `--apply` 新建 0 | D |
| M04 | `test_unreadable_secret_needs_review` | D |
| M05 | `test_migration_preserves_ids_versions_grants_and_accesses`；演练对账（旧授权 3 → 连接授权 3，缺少 / 多出 0） | D |
| M06 | `test_legacy_and_runner_follow_managed_policy` | D |
| M07 | `test_master_key_rotation_keeps_imported_credentials_readable` | D |
| M08 | vitest `puts the target back to the last applied pair after a failed switch`、`keeps the last applied model on failure and re-applies`；`test_model_config_script.py::test_apply_reports_fixed_failure_reasons`（失败时不写入、仍按上一份运行） | V、U |
| M09 | `test_agent_model_bindings.py::test_switching_one_instance_keeps_the_shared_legacy_key` | D |
| M10 | ADR 第 6 节：不迁移 GitHub PAT、Gmail OAuth、服务器密码、新闻模块配置；新闻模块、邮件、智能体服务器、GitHub / Gmail 集成的代码本分支没有改动（`git diff origin/main -- services/api/app/modules/news services/api/app/domain/mail* services/api/app/domain/agent_servers* services/api/app/integrations/gmail* services/api/app/integrations/github*` 为空） | 代码审查 |
| M11 | `test_imported_draft_with_legacy_grants_can_complete_endpoint` | D |
| M12 | `test_classification_roundtrip_preserves_identity_and_governance` | D |
| M13 | `test_classify_non_model_cannot_restore_revoked_access` | D |

### 5.3 OpenClaw 运行时

| ID | 证据 | 类型 |
|---|---|---|
| R01 | `test_bundle_writes_the_managed_provider_without_a_key`（新实例不再默认写 `openai_api_key`）、`test_agent_deployment_requests.py`（部署必须选主模型）；vitest `agent-deployment-flow`；接入包配置用锁定镜像校验通过 | D、V、O |
| R02 | `test_binding_is_desired_until_the_instance_confirms`；场景 27 第 2 步（确认前已应用仍是 A，应用后请求发到 B 的地址、模型与 Key） | D、O |
| R03 | `test_binding_is_desired_until_the_instance_confirms`（迟到回报 409）；场景 27 第 2 步；`model-config.mjs` 用 `apply_key` 判断，相同的不再动手 | D、O |
| R04 | `test_stale_jobs_show_unknown`、`test_apply_reports_fixed_failure_reasons`、`test_unreadable_config_needs_manual_handling` | D、U |
| R05 | 接入包 `CHECKLIST.md` 的主模型一节与 cron 行；`test_agent_ssh_install.py`（7 行 cron 含 `model-config.mjs`）；手动接入未执行时保持“待应用” / “状态待确认” | D |
| R06 | 场景 27：同一台服务器上的乙在甲切换时不重启、配置不变、仍用连接 A | O |
| R07 | `test_patch_only_touches_managed_providers_and_primary`、`test_patch_keeps_a_customised_openai_provider_and_same_primary`；场景 27 第 2 步（fake provider、MCP、IM 渠道、工具限制保留，exec 仍 deny） | U、O |
| R08 | `test_instances_only_reach_their_own_credentials`（停用、撤权拒绝取 Key 与配置）、`test_revoked_removes_the_managed_provider_and_its_primary`；场景 27 第 4 步（解除后立即取不到 Key） | D、U、O |
| R09 | `test_agent_deployment_requests.py`（实例、授权、绑定、应用任务同一事务；重复请求幂等）；`test_assignments_picker_and_legacy`（新建时的选择器） | D |
| R10 | `test_binding_is_desired_until_the_instance_confirms`（切换未确认时 A、B 都能取，确认后 A 退休）；场景 27 第 2 步 | D、O |

### 5.4 安全与界面

| ID | 证据 | 类型 |
|---|---|---|
| S01 | `test_owner_workspace_isolation`、`test_owner_only_and_cross_workspace_is_not_found`、`test_instances_only_reach_their_own_credentials`；`test_security_matrix.py`（每个私有接口要会话、每个写接口要 CSRF、viewer 不能写） | D |
| S02 | `test_connection_crud_never_returns_key`、`test_key_never_appears_in_responses_audit_or_logs`、`test_bundle_writes_the_managed_provider_without_a_key`；vitest `test_saved_key_is_never_revealed_or_submitted_blank`；`sprint6_browser.cjs` 检查所有接口响应里没有 Key | D、V、B |
| S03 | `test_outbound_policy_prevents_secret_exfiltration`、`test_every_protocol_shares_network_constraints`、`test_production_transport_keeps_tls_verification_and_no_redirects`、`test_oversized_non_json_listing_is_invalid`、`test_rate_limit_and_in_flight_lock` | U、D |
| S04 | `test_legacy_and_runner_follow_managed_policy`、`test_legacy_secret_writes_are_governed`、`test_instances_only_reach_their_own_credentials` | D |
| U01 | `sprint6_browser.cjs`（390px 下中转站管理页无横向滚动）；vitest `test_keyboard_tabs_and_focus_return`（方向键切换页签、关闭抽屉后焦点回到打开它的那一行；写这个测试时发现方向键切换后焦点没跟过去，已修）；自查截图 1440 亮 / 暗、390（只在本会话里，没有提交） | B、V |
| U02 | vitest `test_statuses_do_not_collapse_to_empty`、`test_connections_list_filters_total_and_summary`（加载失败与真实空态分开）、`asks before removing the binding and explains a not-granted error`（未授权与版本冲突的说明）；后端 `test_version_conflict_and_idempotent_create` | V、D |
| U03 | vitest `test_workspace_switch_and_stale_responses` | V |
| U04 | 汇总、迁移计数、检测结果都来自 `/summary`、`/migration/entries` 与连接的 `last_check`；vitest `test_connections_list_filters_total_and_summary` | V |
| U05 | vitest `test_migration_drafts_and_non_model_credentials`（旧深链接跳转、兼容凭据可管理）；`sprint6_browser.cjs` 第一步 | V、B |
