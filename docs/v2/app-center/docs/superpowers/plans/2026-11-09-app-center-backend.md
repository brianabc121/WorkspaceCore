# App Center Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, or superpowers:subagent-driven-development if the human explicitly selects parallel execution. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 为现有应用中心提供安全管理元数据、准确健康状态和可恢复的启停同步记录。  
**Architecture:** 保留现有模块list/update与注册表；新增admin管理投影，复用探测器与审计；目标版本和同步结果独立持久化。  
**Tech Stack:** Python3.12、FastAPI、Pydantic2、SQLAlchemy2、PostgreSQL、Alembic、现有HTTP/健康缓存。  
**Spec:** ../specs/2026-11-09-app-center-design.md、../specs/2026-11-09-app-center-contract.md。

## Global Constraints

- 基线4760e34；执行时以最新origin/main、AGENTS及当前Sprint批准为准。
- owner启停/admin只读；新健康检查允许owner/admin，不因此允许其修改模块开关。
- 缺部署配置仍可启用；停用不删数据；核心功能不强制模块化。
- GET管理投影不能主动触发外部HTTP；probe不接受任意客户端URL/导入路径。
- state_version是目标状态版本，version仍是模块发布版本字符串。
- 同步失败不回滚已提交enabled，旧结果不能覆盖新目标。
- 生成OpenAPI及schema，禁止手写生成类型；迁移保持单head，不drop业务数据。
- 新文件路径为建议；若主干已有同责设施则复用，记录实际路径。

## Review Focus

1. 缺部署配置却被优化为禁止启用：AC-1/2有兼容测试。
2. ready被当健康、GET隐式探测：AC-1测试必须证明零网络请求。
3. 同步hook失败/崩溃/超时：AC-2保留enabled与可查询结果。
4. 并发依赖开关和旧版本回包：AC-2多连接测试锁与状态版本。
5. 当前模块活动误混入其他模块/工作空间：AC-1精确资源过滤与游标测试。

## 文件地图

修改：
- services/api/app/modules/service.py、base.py（仅必要扩展）、registry.py（扩展点复用，不新建第二注册表）
- services/api/app/models/module.py、schemas/modules.py、api/v1/modules.py
- services/api/app/domain/operations.py（复用健康/cache能力，保持既有接口）
- app/api/deps.py（管理服务依赖注入，按实际注册位置）
- migrations/versions/<当前head之后的新revision>.py
- services/api/openapi.json、apps/web/src/services/schema.d.ts（生成）
- docs/modules/README.md、docs/v2/MODULES_PLAN.md（仅记录新管理能力，不改协作协议）
新增建议：
- services/api/app/modules/management.py：admin投影、声明能力、安全健康聚合。
- services/api/app/modules/news/hooks.py、news/service.py：真实render结果与所有共享写入口的协调。
- services/api/app/domain/module_sync.py：持久attempt、版本、操作锁和重试协调器。
- services/api/app/schemas/module_management.py：管理/健康/同步投影。
- tests/test_module_management.py、test_module_sync.py、test_module_state_migration.py。
回归：tests/test_modules.py、test_navigation_api.py、test_operations.py、test_news_api.py、test_news_config.py。

## AC-0：复核基线与契约

**Consumes:** 本包、最新main、现有模块/导航/审计/健康实现。
**Produces:** 正式任务paths、接口映射与实施前差异记录。

- [ ] fetch主干，运行collab inbox --as自身身份，查看并发与已有PR，保护脏工作。
- [ ] 核对news仍是唯一模块；新模块若已出现，管理页通过注册表自然呈现，不硬编码news。
- [ ] 完整读ModuleManifest、ModuleService、news hook、Operations健康缓存、Navigation整份更新与相关测试。
- [ ] 将本包AC编号映射成正式任务，记录相对于4760e34的变化、现有迁移head与需要修改的契约。
- [ ] 冻结建议新增字段/路径并写简短ADR：状态版本、同步结果持久化、锁边界与导航复用。
- [ ] 提交任务/ADR或按仓库流程准备首PR，不修改他人已占用任务。

## AC-1：能力投影、健康与精确活动记录

**Files:** modules/management.py、schemas/module_management.py、api/v1/modules.py、operations.py、tests/test_module_management.py。
**Consumes:** 现有registry、ToolSpec、ServiceCheckSpec、ServiceCache、审计记录；AC-0契约。
**Produces:**
- ModuleManagementService.list(access) -> ModuleManagementList
- ModuleManagementService.get(access,key:str) -> ModuleManagementOut
- ModuleManagementService.check_health(access,key:str) -> ModuleHealthOut
- ModuleManagementService.activity(access,key:str,cursor:str|None,limit:int) -> Page[AuditLogView]
以上类型/字段以contract为准。

- [ ] 写test_management_role_and_scope：owner/admin可读，普通成员仍能GET/modules但不能新管理API；跨workspace隔离。
- [ ] 写test_management_get_does_not_probe：注入计数probe，GET列表/详情只读缓存，ready与unknown健康独立。
- [ ] 写test_health_check_is_declared_bounded_and_enabled：只调该模块probe；disabled拒绝，缺requires不出站，无probe为not_applicable。
- [ ] 写test_capabilities_are_declarations：真实news给7个read工具/1cron/1widget；不声称实例已授权。
- [ ] 写test_health_aggregation_and_attention：缺配置、down/degraded、unknown、混合检查和每模块去重统计；disabled但停用同步失败仍算attention。
- [ ] 写test_module_activity_exact_resource：news与news_digest、其他resource、其他workspace不会混合；cursor变scope/模块拒绝。
- [ ] 执行上述测试保存真实失败；实现最小聚合与角色验证、静态路由优先顺序、脱敏。
- [ ] 复用现有ServiceStatusOut、OutboundPolicy和probe超时；GET无副作用，显式检查限流。
- [ ] 运行新测试与test_modules/test_operations，导出OpenAPI，再提交。

## AC-2：版本化启停与可观察同步

**Files:** models/module.py、schemas/modules.py、modules/service.py、domain/module_sync.py、迁移、新sync/迁移测试。
**Consumes:** ModuleService启停/依赖规则、现有on_toggle签名与审计。
**Produces:**
- state_version与ModuleSyncAttempt扩展迁移。
- ModuleSyncCoordinator.run_current(access,key,expected_state_version:int) -> ModuleSyncOut
- 同一跨进程workspace操作锁供旧/新PATCH与reconcile复用。
- POST/modules/{key}/reconcile与管理投影的sync字段。

- [ ] 写test_upgrade_preserves_workspace_enablement：旧行state_version=0，无行仍default_enabled，业务表/原设置/审计不变。
- [ ] 写test_versioned_toggle_is_idempotent_and_legacy_compatible：新版本冲突409；旧body仅enabled仍可调用；相同目标不重复hook/审计。
- [ ] 写test_missing_requirement_does_not_block_enable：继续允许ready=false启用并显示待配置。
- [ ] 写test_real_news_render_error_is_reported：在真实render_current路径让write_files抛OSError或解密失败，非空错误字符串必须变成failed；未部署/业务未开为not_required，不能仅mock hook抛异常。
- [ ] 写test_settings_worker_feedback_and_disable_share_render_lock：两个真实DB连接与可控屏障，设置/worker/反馈在停用前读取或排队，停用后最终文件仍为禁用；部署级资源锁不能按workspace分割，资源锁须先于业务行锁。
- [ ] 写test_hook_failure_keeps_target_and_records_outcome：hook异常不回滚enabled；failed类别可查，不泄露异常正文。
- [ ] 写test_timeout_and_interruption_are_unknown：hook超时/进程中断没有伪成功；无hook为not_required，旧无记录为unrecorded。
- [ ] 写test_parallel_dependency_toggle_and_reconcile：两个独立数据库连接竞争，依赖不变量成立，锁忙无写入；不能只测单进程lock。
- [ ] 写test_old_attempt_cannot_override_new_target：旧终态保留历史但不作为当前sync；retry按最新版本且不修改enabled。
- [ ] 先运行失败测试，再实现扩展迁移、正确版本递增、跨提交持锁、有界hook与独立结果事务。
- [ ] 给reconcile加owner+CSRF、固定目标/导入路径、在途保护；结果/审计脱敏。
- [ ] 给news hook增加结构化HookOutcome并消费render_current的非空错误；扫描设置、反馈排除词、relearn/worker、部署命令及全部写入口，统一资源锁、最新状态读取和固定锁顺序。
- [ ] 新闻早报停用继续关闭对应workspace的采集/推送配置；再启用恢复原设置，read tools/tick/ingest行为不退化。
- [ ] 测试迁移upgrade与回退兼容，跑全模块/新闻回归；生成OpenAPI与schema，提交。

## 后端阶段命令

以下是实施时要执行，不是本包已经跑过：
~~~bash
make lint-api
cd services/api
uv run pytest tests/test_module_management.py tests/test_module_sync.py tests/test_module_state_migration.py
uv run pytest tests/test_modules.py tests/test_navigation_api.py tests/test_operations.py tests/test_news_api.py tests/test_news_config.py
uv run alembic heads
uv run alembic upgrade head
cd ../..
make openapi
python3 scripts/collab.py check
~~~

数据库/Redis相关测试需专用TEST_DATABASE_URL/TEST_REDIS_URL；未配置导致SKIP不能算依赖并发/迁移通过。
