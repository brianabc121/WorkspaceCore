# 源码接入点与现状

基线4760e3471954545c593973862797da21337577b3，最新主干含中转站PR #183。
本次读取统一git show主干，保护旧codex/T-0149分支及其已有未提交工作；未修改产品源码。
逐文件SHA256见source-manifest.json。实施时重新fetch，使用本包设计与实际最新契约对照，不能把基线当永久事实。

| 位置 | 当前职责/接入点 |
|---|---|
| services/api/app/modules/base.py | ModuleManifest/CronSpec/ServiceCheckSpec、HOST_API=1、清单验证 |
| services/api/app/modules/registry.py | 显式MODULES注册，当前只有NEWS；汇总模型/router/工具/cron/health/加密列 |
| services/api/app/modules/service.py | enabled_keys/is_enabled、依赖检查、ModuleService.update、提交后的_on_toggle |
| services/api/app/modules/deps.py | 页面API模块启用检查 |
| services/api/app/models/module.py | WorkspaceModule按workspace+module_key存enabled、updated_by/at；尚无state_version |
| services/api/app/schemas/modules.py | ModuleOut只含基本信息/ready/缺配置/依赖/工具/组件；无完整详情或sync |
| services/api/app/api/v1/modules.py | GET成员可读；PATCH只有owner+CSRF；body仅enabled |
| services/api/app/domain/operations.py | 已有健康probe与cache；GET服务聚合可能探测 |
| services/api/app/modules/news/* | 清单：2页面/1组件/7read工具/1cron；hook重新生成TrendRadar配置 |
| services/api/app/domain/navigation.py | owner整份布局更新、version冲突、目标workspace校验 |
| services/api/app/api/v1/navigation.py | GET按角色过滤、PUT owner完整替换 |
| services/api/app/api/v1/audit.py | 现有audit筛选，尚无精确resource_id参数；q不是精准模块过滤 |
| apps/web/src/pages/AppCenterPage.vue | 基本卡片、部署缺项、依赖和工具，owner开关/admin只读 |
| apps/web/src/stores/modules.ts | 导航启停事实源，user/scope切换防迟到回包，MODULE_DISABLED刷新 |
| apps/web/src/modules/types.ts、registry.ts | defineModule、导航/route/widget/event扩展，暂无management入口字段 |
| apps/web/src/modules/news/index.ts | news的2条nav/route、AI新闻速览、事件声明 |
| features/navigation/NavigationSettings.vue、PageForm.vue | 已有布局草稿/页面添加编辑，整份保存并保留冲突草稿 |
| apps/web/src/app/navigation.ts、router.ts | 现有/settings/modules与/settings/navigation，角色/模块守卫 |
| services/api/tests/test_modules.py | 清单一致性、权限、依赖、停用页面/工具/tick/ingest、再启用设置保留 |
| apps/web/tests/modules.test.ts | 懒加载、导航、开关、组件、权限与MODULE_DISABLED呈现 |
| scripts/new_module.py | 开发期生成骨架并登记，不是运行时安装工具 |
| docs/modules/README.md、MODULES_PLAN.md、feature-modules ADR | 明确模块、零代码页面、待支持外部应用三类边界 |

## 已确认规则

ready只判配置非空，不能冒充探活；缺部署前提允许启用。
on_toggle在开关提交后执行，异常回滚hook事务并只记日志，当前页面看不到执行结果。
工具可见还取决于tools_visible/应用设置和实例既有权限；声明7个工具不等于全部实例实际可用。
任务、邮件、GitHub、OpenClaw、中转站等核心功能不因有菜单就属于MODULES。
模块停用不会删数据或卸载服务；零代码入口的移除不等于删目标对象。
现有外部应用协议T-0162没有通用实现，不提供上传插件或任意外部程序授权。

## 本包没有做的验证

没有运行产品测试、真实数据库迁移或浏览器测试，没有对生产workspace开关做操作。
仅检查源码与已有测试定义；实施计划里的命令和PASS标准不是本次已执行结果。

补充核验：news.service.render_current会捕获OSError/CredentialError/ApiError并以非空字符串返回错误；news.hooks当前忽略返回值。update_settings、relearn_and_render、反馈新增排除词等也写同一TrendRadar配置，需要共享资源锁和明确结果，详见契约补充。
