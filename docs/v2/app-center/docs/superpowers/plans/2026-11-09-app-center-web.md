# App Center Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, or superpowers:subagent-driven-development if the human explicitly selects parallel execution. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 落地应用中心效果图，统一模块详情和页面入口管理。  
**Architecture:** 宿主应用中心依赖已有module/navigation事实源；管理投影补元数据，路由/设置入口从ModuleDefinition声明。  
**Tech Stack:** Node22、npm ci、Vue3、TypeScript、Pinia、Vue Query、openapi-fetch、现有Drawer/Modal/设计tokens。  
**Spec:** ../specs/2026-11-09-app-center-design.md、../specs/2026-11-09-app-center-contract.md；assets/01-app-center.png。

## Global Constraints

- 页面仍为/settings/modules，应用中心本身为不可随意关闭的宿主核心入口。
- owner可改/admin只读；模块页面与资源的原角色规则不因新入口改变。
- 前端Node22，OpenAPI先更新，再生成schema.d.ts；不手工伪造API字段。
- ready、enabled、health、sync分开；图中数字与3个自定义页面不写死。
- 页面入口通过同一navigation服务整份版本化保存，不另建Page表。
- 不增加ZIP安装、在线升级、清除数据、任意应用授权。
- 暗色/390移动/1440桌面/键盘与焦点状态必须验证。

## Review Focus

1. 模块名/路由/能力来源分叉：AC-3一致性测试，不能硬编码news。
2. 切scope与迟到状态回包：AC-3隔离请求，保留真实当前目标。
3. 启停网络超时被误报成功/被反向回滚：AC-3先拉取真实状态。
4. 页面入口编辑覆盖其他布局：AC-4保留完整快照，409保草稿。
5. 降低导航可见角色被误当资源授权：AC-4不能绕后端权限。

## 文件地图

修改：
- apps/web/src/pages/AppCenterPage.vue：薄页面与宽版布局。
- apps/web/src/stores/modules.ts：现有导航开关事实源，补新版本/结果处理。
- apps/web/src/modules/types.ts、modules/registry.ts、modules/news/index.ts：管理入口声明。
- apps/web/src/app/router.ts：宽版与query定位，保留既有路由守卫。
- features/navigation/PageForm.vue、NavigationSettings.vue、stores/navigation.ts（抽取复用，不复制整套规则）。
新增建议：
- features/app-center/api.ts、viewModel.ts、AppCenterWorkspace.vue、ModuleCard.vue、ModuleDetail.vue、ModuleCapabilities.vue、ModuleActivity.vue、CustomPagesPanel.vue、ModuleGuide.vue、app-center.css。
- tests/app-center.test.ts、app-center-pages.test.ts。
回归：tests/modules.test.ts、navigation.test.ts；后端生成schema.d.ts。

## AC-3：应用中心列表、详情与状态

**Consumes:** AC-1管理投影/健康、AC-2版本化sync、原module/navigation store。
**Produces:** /settings/modules主视图，?tab=modules&module=<key>深链接，ModuleDefinition.management。
- buildAppCenterViewModel(backendModules,frontendRegistry,visiblePages) -> AppCenterViewModel
- useModuleManagement(scope)、useModuleDetails(scope,key)、checkModuleHealth(scope,key)等强类型hooks。
公开类型从生成Schema推导，临时UI字段在viewModel内定义，不复制后端模型。

- [ ] 写test_management_entries_follow_module_registry：news两个入口；无settingsPath不渲染设置按钮；前后端key不一致不猜路径。
- [ ] 写test_enabled_ready_health_sync_are_independent：启用但待部署、ready但未探测、hook失败但enabled仍保存分别呈现。
- [ ] 写test_declared_capability_counts_not_runtime_grants：2页/1组件/7read工具/1cron为声明数，不写“已授权所有智能体”。
- [ ] 写test_owner_toggle_admin_read_only：只有owner发送PATCH/reconcile；admin可调用允许的健康检查，普通成员不能打开中心。
- [ ] 写test_scope_and_request_revision_isolation：换用户/workspace清空选择和请求，迟到回包不污染新状态。
- [ ] 写test_network_unknown_refreshes_without_double_toggle：请求超时先GET真实enabled/version/sync；不盲目重发或自动反向开关。
- [ ] 写test_search_and_summary_use_complete_set：筛选不改变全局计数；无模块、接口失败和stale区分。
- [ ] 实现模块卡片/详情、四类状态、依赖说明、停用确认、操作记录，复用现有组件与tokens。
- [ ] 补management元数据，新闻仅在自身index.ts声明路径；不要在宿主写key===news。
- [ ] 列表GET不自动探测，详情提供显式检测；仅sync在途时有界刷新，离开/切scope取消。
- [ ] 跑新测试与modules.test.ts；生成类型检查通过后提交。

## AC-4：统一页面入口管理

**Consumes:** 原GET/PUT/navigation、PageForm、owner完整布局与version。
**Produces:** 自定义页面tab及快捷预览，?tab=pages定位。

- [ ] 写test_page_edit_preserves_unrelated_layout：编辑一项后其余pages/sections/placements/order字节等价（除必要规范化）。
- [ ] 写test_navigation_conflict_preserves_draft：另一标签先保存，409后保留草稿，重新读取后显式确认再提交，不静默覆盖。
- [ ] 写test_owner_snapshot_not_lower_role_cache：admin只读，无PUT；owner不复用上次较低角色过滤过的布局。
- [ ] 写test_remove_entry_does_not_delete_target：移除note/dashboard链接仅更新导航，不调用目标删除API。
- [ ] 写test_entry_role_is_not_resource_permission：降低min_role不能读到无权限资源；embed仍遵守允许来源和sandbox。
- [ ] 复用PageForm和导航草稿/保存逻辑，抽取共享helper，保留原“侧栏与页面”布局编辑能力。
- [ ] 新页点确认后显式保存单次完整快照，更新NavigationStore并通知原页面；图中3项入口来自真实列表。
- [ ] 核验最多50页/12分组等原校验，422展示目标项字段错误；未保存离开给清晰提示。
- [ ] 跑新页面入口测试及navigation.test.ts，提交。

## AC-5：接入指南、可访问性与整体回归

**Consumes:** AC-3/4完整功能、现有模块开发指南。
**Produces:** 接入说明、外部应用待支持占位、真实浏览器截图与交付证据。

- [ ] 写test_guide_does_not_offer_runtime_install：区分开发注册与零代码入口；无ZIP安装/升级/清数据按钮。
- [ ] 写test_activity_is_module_scoped：对news的详情不展示其他模块记录，cursor/选项改变时重新加载。
- [ ] 写test_disabled_pages_widgets_tools_restore：保持已有停用/恢复能力；应用设置打开遵守原路由角色。
- [ ] 核验桌面1440、移动390、暗色、键盘、Esc、焦点回位、面板关闭/深链接不存在模块。
- [ ] 浏览器覆盖启用未部署、健康超时、同步失败重试、409版本冲突、零代码新增/编辑/移除、scope切换。
- [ ] 使用专用测试workspace，全部状态来自测试数据/API，不在生产塞示例模块或伪造健康。
- [ ] 跑下列前端命令与collab check；按AC-6验证矩阵完成交付，提交PR等审查。

~~~bash
node --version
npm --prefix apps/web ci
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run test
npm --prefix apps/web run check:api
npm --prefix apps/web run build
python3 scripts/collab.py check
~~~

先确认node为22.x；check:api会重生成类型，保护其他人的未提交文件。
浏览器自动化入口沿用项目现有方式或本任务新增明确脚本；在报告中写确切命令和退出码，本包不编造未存在的浏览器脚本。
