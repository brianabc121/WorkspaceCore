# Model Connections Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. If the human selects parallel execution, use superpowers:subagent-driven-development. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 落地中转站管理效果图与OpenClaw选择连接/模型的流程。  
**Architecture:** Vue Query按user+workspace隔离；管理页、详情抽屉、模型/授权、绑定表单各自独立；真实API强类型。  
**Tech Stack:** Node 22、npm ci、Vue 3、TypeScript、Vite、Vue Query、openapi-fetch、现有组件与设计tokens。  
**Spec:** ../specs/2026-11-09-model-connections-design.md、同目录contract；assets/01-model-connections.png。

## Global Constraints

- 依赖后端实际OpenAPI；禁止前端自行发明字段或手写schema.d.ts。
- 搜索/分页/汇总取真实API，不用当前加载条数做全局统计。
- 所有已存Key不回显，表单关闭/成功后清空；不得放localStorage。
- URL/Key/协议/模型是独立概念，品牌名称不是模型ID。
- 支持暗色、窄屏、键盘、loading/error/stale/empty/permission状态。
- 旧密钥入口要重定向；保留兼容凭据操作，避免Runner退化。
- 图片中的4/3/1/6、检测通过、迁移数量全是样例，禁止写死到生产。

## Review Focus

1. 同workspace不同用户切换/迟到请求：MC-6证明不会展示上一身份数据。
2. 元数据成功与推理成功混淆：MC-6分级文案测试。
3. API Key更换空输入覆盖原值：MC-6只在独立操作提交非空Key。
4. 未配置旧项出现在新部署选项：MC-7证明不可选并提示补全；创建态不按尚未存在实例的Grant筛空列表。
5. 离线/失败/未知应用：MC-7证明保存成功不会显示运行中已切换。

## 文件地图

新增建议：
- apps/web/src/pages/ModelConnectionsPage.vue
- apps/web/src/features/model-connections/api.ts、types.ts、ModelConnectionsWorkspace.vue
- ConnectionTable.vue、ConnectionDrawer.vue、ConnectionForm.vue、ConnectionModels.vue、ConnectionGrants.vue、ConnectionActivity.vue、MigrationPanel.vue、model-connections.css
- apps/web/src/features/agents/ModelBindingPanel.vue、ModelConnectionPicker.vue
- apps/web/tests/model-connections.test.ts、model-bindings.test.ts
修改：
- app/navigation.ts、app/router.ts，必要时stores/navigation.ts的旧path兼容逻辑。
- pages/SecretsPage.vue、features/agents/SecretsWorkspace.vue（整理为兼容凭据入口，不制造第二套模型Key编辑）。
- features/agents/AgentDeployDrawer.vue、InstanceForm.vue、InstanceCard.vue、AgentsWorkspace.vue。
- 对应现有agents/navigation/deployment/runner测试。
生成：apps/web/src/services/schema.d.ts，仅用npm run generate:api。

## MC-6：中转站管理与迁移页

**Consumes:** MC-1/2/3真实接口、生成Schema、视觉效果图。
**Produces:** /settings/model-connections，以及旧路由兼容。

- [ ] 写test_connections_list_filters_total_and_summary：服务端分页/搜索正确；summary独立于筛选；空列表不同于加载失败。
- [ ] 写test_official_relay_and_multi_model_form：官方URL、第三方base URL、指定多模型、全部可用模型；不支持列表时提示手工填写。
- [ ] 写test_saved_key_is_never_revealed_or_submitted_blank：GET无值、独立更换Key、取消/完成清理内存表单；不做原值复制。
- [ ] 写test_statuses_do_not_collapse_to_empty：429显示限流/retry_at，401提示凭据，unsupported不显示空列表成功，旧检查显示时间。
- [ ] 写test_migration_drafts_and_non_model_credentials：旧导入待完善、待归类可操作，非模型Token仍能管理，未自动创建新模型绑定。
- [ ] 写test_workspace_switch_and_stale_responses：卸载/切scope清理敏感状态，迟到回包不跨workspace。
- [ ] 实现API hooks、表格/抽屉/表单/模型/授权/活动，复用组件；表单的保存、检测、同步各有独立busy/error。
- [ ] 提供“复制为新连接”预填非敏感字段，Key需重新输入或后端显式同scope复制（本期默认重新输入），不从浏览器拉回旧Key。
- [ ] 实现有引用删除冲突与影响提示、旧URL跳转和导航兼容；若用旧path保留稳定ID需补redirect测试。
- [ ] npm run test -- tests/model-connections.test.ts；记录真实失败→通过；提交。

## MC-7：智能体连接分配、部署与应用状态

**Consumes:** MC-4/5绑定/应用、MC-6连接选择器。
**Produces:** 管理页分配表，部署抽屉和详情面板的连接+模型选择。

- [ ] 写test_picker_only_offers_ready_enabled_authorized_connections：已有实例只列可用授权项；新建实例列owner可管理的ready+enabled连接并说明选择即授权，不能陷入先有实例才能选连接的循环；模型列表为明确ID，可搜索并分页。
- [ ] 写test_save_does_not_claim_applied：保存后pending；job成功+对应revision确认后才applied；失败/unknown/manual_required有正确操作。
- [ ] 写test_deploy_requires_valid_model_binding：不能落回固定openai/openai_api_key；legacy已运行实例仍显示旧配置待迁移。
- [ ] 写test_used_model_removal_and_revoke_need_resolution：有绑定模型/授权不能直接移除，提示先改绑定。
- [ ] 修改部署抽屉与实例详情，主配置不再让用户手填ref_id；高级凭据授权仅用于非模型/迁移兼容。
- [ ] 分配表显示目标与已应用模型；重试复用job，后台轮询仅限在途任务，离开页面停止。
- [ ] 保留IM、MCP、唤醒、邮箱、资源上限、运维与旧Runner权限UI；不把“在线”当成配置已应用。
- [ ] 浏览器核验桌面1440、窄屏390、暗色、键盘与错误态；保存截图与操作路径，提交。

## MC-8：端到端交付

**Consumes:** MC-0～7全部完成。
**Produces:** 验收报告、迁移/回退演练、PR真实检查输出。

- [ ] 按acceptance测试矩阵逐项验收，迁移前后对账和一机双实例必须有证据。
- [ ] 查阅最新主干是否已有同类PR/新ADR；解决契约漂移再交付。
- [ ] 跑下列命令；记录stdout/退出码与实际环境，不把预期结果写成已通过。
- [ ] 对照效果图和文本规则核验；更新正式任务验收状态，按PR模板写交付说明。
- [ ] 交付PR及链接，遵守仓库审查/合并职责，不将生产迁移当成自动获批的破坏性操作。

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

先确认node为22.x；check:api会重新生成类型，运行前保护其他人的未提交生成文件。新增API类型的生成结果与后端OpenAPI一起交付。
