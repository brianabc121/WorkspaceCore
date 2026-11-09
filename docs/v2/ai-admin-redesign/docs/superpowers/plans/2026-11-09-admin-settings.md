# 六页管理设置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 人类交给Claude落实，当前Codex只交文档；技能不可用时按等价步骤执行，不为读方案安装插件。

**Goal:** 优化设置、集成、安全、邮件、新闻早报和密钥库六页，保持现有权限与数据行为。

**Architecture:** 从设置页建立少量共享管理布局，再依次重排各领域组件。前五页尽量复用现有契约，密钥库补服务端搜索／元数据详情；不抽取新插件框架，不重写认证服务。

**Tech Stack:** Vue 3、TypeScript、Vite、Pinia、TanStack Vue Query、现有UI/CSS；后端FastAPI/Pydantic/SQLAlchemy。前端Node 22、npm ci。

**Spec:** [八页规范](../specs/2026-11-09-ai-admin-design.md)§2–3/5–10、[契约](../specs/2026-11-09-ai-admin-contract.md)§1/3–4。基线origin/main e0176b59ffcf2ae3e630d4d1b0f75addcf4b1f6c，开工重新fetch。

## Global Constraints

- 保持权限矩阵、user/workspace scope、CSRF、version、SSE、幂等；不扩大admin权限。
- 存量Token／Secret／Webhook不回显；敏感表单提交／关闭／换对象清空，不进持久缓存或错误详情。
- 每个实体独立保存；draft与saved snapshot分离，刷新不覆盖dirty，409核对最新值。
- GitHub只读，最多20仓库；Gmail readonly+send；新闻owner写/admin只读；密钥库owner。
- 日期、回调、配置版本、对象ID全部来自接口，图片样例不硬编码。
- 不加入MFA／头像上传／邀请邮件／Gmail modify，不把侧栏折叠固定为新全局缺省。
- 新闻留在模块注册与目录；密钥库保留，不受服务器SSH密钥退役方案误删。
- 无需新业务表；生成类型机械产生。只改本任务paths，不覆盖其他在途代码。

## Review Focus

1. 成员角色／最后owner、当前会话撤销、密码错误：按原权限处理，不借UI扩大能力（B1/3）。
2. Token限流／未核验与旧快照：未知不变通过，失权不展示旧私有数据（B2）。
3. 多邮箱快速切换、署名与分配并发：不串账号、不覆盖授权、不顺便开通发信（B4）。
4. 新闻dirty/配置渲染失败/模块停用/其他空间占用：不假成功，不触发生成／推送（B5）。
5. 超过一页密钥、选中项失权／删除、轮换冲突：元数据可查，值永不回显（B6）。

---

## 开工与顺序

- [ ] 读最新AGENTS.md/docs/AGENTS.md、.collab及相关Github/Gmail/news/密钥/角色契约；检查owner和paths占用。
- [ ] git fetch origin main；python3 scripts/collab.py inbox --as claude；记录本次人类指派，建／接手实际T编号并start，保护未提交工作。
- [ ] B1共享组件先落地，再B2–B6；AI与审计前端复用B1。路由／OpenAPI／生成类型串行改。可以逐页PR，也可同分支按阶段提交；每次合入均有完整可用行为。

## Task B1：设置与共享管理布局

**Files**
- Create: apps/web/src/components/management/ManagementPage.vue、ManagementSection.vue、management.css（主干已有等价组件就复用，不复制）
- Modify: apps/web/src/pages/SettingsPage.vue、src/features/ProfileForm.vue、WorkspaceForm.vue、MembersPanel.vue
- Modify: apps/web/src/app/router.ts（只为本轮核心页显式wide，新闻在模块meta配置）
- Regress: apps/web/tests/members.test.ts、auth.test.ts；新增有必要的settings-redesign.test.ts行为用例。

**Interfaces**
- ManagementPage props={title:string,description?:string}，slots default/actions/aside；只管布局，不发请求或持有业务表单。
- ManagementSection props={title:string,description?:string}，slots default/actions；状态反馈沿现有DataState/ActionFeedback。
- Profile／Workspace／Members继续原API与各自保存。成员添加保留existing-user或显式create-account，不发送邀请邮件。

- [ ] **Step 1 — 失败用例：** 保留owner不可编辑／移除，admin不可改admin；仅改资料不提交workspace，切空间旧响应不改新空间，dirty不会被刷新抹掉。未找到用户时引导显式创建，不显示已邀请。
- [ ] **Step 2 — 确认失败：** 运行相关members／settings行为测试，确认新增用例因未实现交互失败，不为纯改文案写镜像测试。
- [ ] **Step 3 — 实现：** 两张独立资料卡、成员表和快捷入口，邮箱只读，按钮局部pending／反馈；角色控件按当前auth和后端规则，沿原name长度。布局对其他页面无全局影响。
- [ ] **Step 4 — 通过／提交：** 跑相关现有测试、实际窄宽／亮暗／键盘验收和README前端门槛；共享组件和自己的任务记录一起提交。

## Task B2：GitHub集成页

**Files**
- Modify: apps/web/src/pages/IntegrationsPage.vue、src/features/integrations/IntegrationsWorkspace.vue、GitHubForm.vue、IntegrationChecks.vue
- Create: apps/web/src/features/integrations/RepositoryPermissionTable.vue、IntegrationConnectionPanel.vue、integrations.css
- Reuse: 现有SyncNotice、集成API与developer仓库权限证据，不再造后台探针。
- Regress: apps/web/tests/github-settings.test.ts；services/api/tests/test_github_permission_evidence.py、test_integrations_api.py（确涉后端时）。

**Interfaces**
- IntegrationTestResult.repositories或GET developer/repos/{repo_id}/permissions提供四项实际结果；repo_id按当前集成／仓库全名匹配现有投影。
- 原save/test/request-sync分开；测试结果与持久快照标明checked_at，不从access=ok构造四项成功。

- [ ] **Step 1 — 失败用例：** 无快照／只执行metadata时其余未核验；Token变更失效、unauthorized清缓存、rate_limited显示retry_at。只读仓库缺Actions不把其他项抹掉；20仓库限制、大小写去重和经典Token拒绝保持。
- [ ] **Step 2 — 确认失败：** 在github-settings添加新增行为后运行该文件，保留真实失败。
- [ ] **Step 3 — 实现：** 连接／同步状态、权限表、修复引导与修改连接侧栏；Token留空保留原值，存量不回显／复制；显示到期字段但不解析密钥猜日期。普通刷新不测试／同步，Webhook不支持只作说明。
- [ ] **Step 4 — 通过／提交：** 新旧GitHub测试及正常／部分缺权／限流／失权截图通过；若只是复用#179后端，不修改它的生成类型或重复加缓存。

## Task B3：密码与本人会话

**Files**
- Modify: apps/web/src/pages/SecurityPage.vue、src/features/PasswordForm.vue、SessionList.vue
- Create: apps/web/src/features/security/SessionRow.vue、security.css（若现有目录已迁移按新位置）
- Test: apps/web/tests/security-settings-redesign.test.ts（新）
- Regress: services/api/tests/test_sessions_api.py、test_auth_api.py与现有前端auth测试。

**Interfaces**
- confirm_new_password只在前端验证，不加后端字段。密码12–128，仍提交current_password/new_password。
- SessionRow props={session:SessionOut,busy:boolean}，发出revoke(session.id)，不推导设备／城市／在线状态。

- [ ] **Step 1 — 失败用例：** 新旧密码错／确认不符不误报成功；修改成功其他会话撤销、当前保留。撤销当前会话退出并清私有缓存，撤销其他只更新其列表；不能选择别人的session_id。
- [ ] **Step 2 — 确认失败：** npm run test -- tests/security-settings-redesign.test.ts。
- [ ] **Step 3 — 实现：** 密码表单＋当前/其他会话卡，创建／最近活动／到期按接口；确认对话有焦点管理，密码尝试后清空。账户级查询键按user，不误当workspace成员会话。
- [ ] **Step 4 — 通过／提交：** 新旧auth/session相关回归和手机／桌面／键盘通过；不加安全分、MFA或不存在的设备信息。

## Task B4：多邮箱配置与智能体分配

**Files**
- Modify: apps/web/src/pages/MailSettingsPage.vue、src/features/mail/MailSettings.vue、MailAssign.vue、api.ts
- Create: apps/web/src/features/mail/MailAccountSettingsTable.vue、MailAccountSettingsPanel.vue、GoogleClientSettingsCard.vue、mail-settings.css
- Test: apps/web/tests/mail-settings-redesign.test.ts（新）
- Regress: services/api/tests/test_mail.py、test_mail_tools.py；前端mail-pages/mail-aggregate相关用例。

**Interfaces**
- 使用MailSettingsOut的redirect_uri/client_secret_set及MailAccountOut.can_read/can_send/status/agents/version；不要另建OAuth客户端配置服务。
- 表格按account_id选详情；分配PUT与署名PATCH分开、用各自最新account.version。客户端secret留空保持，输入原文不回填query cache。

- [ ] **Step 1 — 失败用例：** 三邮箱中一个needs_reconnect，列表不消失；切邮箱不串分配draft。新连邮箱agents=[]，分配不自动打开send_enabled。署名保存后分配用更新version，409核对最新数据，不覆盖其他账号。
- [ ] **Step 2 — OAuth与隐私：** 回调地址来自接口；重复回调／失败提示沿当前机制。can_read/can_send分别显示；断开撤销授权但保留发件记录。密码／secret不入URL、持久化或错误input，设置页不请求正文。
- [ ] **Step 3 — 确认失败：** npm run test -- tests/mail-settings-redesign.test.ts。
- [ ] **Step 4 — 实现：** 顶部OAuth状态、多邮箱表与所选账号面板；使用原connect/reconnect回调、现有授权名单和署名。当前邮箱发送scope不代表所有智能体可发送，保留去实例邮件权限入口。
- [ ] **Step 5 — 通过／提交：** 新旧mail测试与OAuth fake／授权／跨空间浏览器用例通过；不真实连接用户Google账号来做视觉验收，不新增modify scope或默认授予所有智能体。

## Task B5：新闻早报配置分组与生效状态

**Files**
- Modify: apps/web/src/modules/news/NewsSettingsPage.vue、NewsSettings.vue、NewsSecretInput.vue、settingsForm.ts、api.ts、news.css、index.ts（只本设置路由wide meta）
- Create: apps/web/src/modules/news/NewsScheduleSettings.vue、NewsSettingsStatus.vue、NewsChannelSettings.vue
- Test: apps/web/tests/news-settings-redesign.test.ts（新）
- Regress: apps/web/tests/news-pages.test.ts；services/api/tests/test_news_api.py、test_news_config.py、test_modules.py。

**Interfaces**
- 继续NewsSettingsOut/NewsStatusOut/保存body及版本，沿已有form构造／校验，子组件只编辑共同draft并emit局部更新，不各自偷偷保存半套配置。
- tabs分基本与时间／来源／兴趣／模型渠道／学习偏好；所有原fields均可达。secret用is-set显示，不把占位“已设置”提交成原值。

- [ ] **Step 1 — 失败用例：** owner可写、admin只读且无test-push/generate写请求，停用MODULE_DISABLED无设置请求或硬编码导航；无部署／taken_by_other／online=null如实显示。timezone只读、1–3个半小时时刻。
- [ ] **Step 2 — 状态用例：** version冲突／SSE到新配置不抹dirty；保存成功但render_error非空不报全成功。config_rev与version分开，rendered_at不说已运行。所选实例被删／停用不替换为另一实例。
- [ ] **Step 3 — 副作用用例：** 普通刷新只GET；dirty时先保存再测试／生成，写操作只用已保存配置。测试渠道有失败时保留逐渠道结果，不说全部送达；learned建议不自动关闭来源。
- [ ] **Step 4 — 确认失败：** npm run test -- tests/news-settings-redesign.test.ts。
- [ ] **Step 5 — 实现：** 拆长表单为分组组件与状态侧栏，保留两模式、全部来源／topic／模型／渠道／学习配置。图片“运行中的v12”改成“最近写入配置#12”，secret只写，图中已配置不等于可达／已送达。
- [ ] **Step 6 — 通过／提交：** 新旧news和模块测试、配置失败／只读／停用／手机截图通过；不改生成协议、调度和权限，不迁出news模块。

## Task B6：密钥库服务端搜索、详情与取用记录

**Files**
- Modify: services/api/app/domain/agents.py、app/api/v1/agents.py、app/schemas/agents.py
- Create: services/api/tests/test_secret_catalog_queries.py
- Modify: apps/web/src/pages/SecretsPage.vue、src/features/agents/SecretsWorkspace.vue、queries.ts
- Create: apps/web/src/features/agents/SecretCatalogTable.vue、SecretDetailPanel.vue、secrets.css
- Create: apps/web/tests/secret-catalog-redesign.test.ts
- Regress: services/api/tests/test_agents_api.py、test_oc6_security.py、test_credentials.py；现有agents前端用例。
- Generate: services/api/openapi.json、apps/web/src/services/schema.d.ts

**Interfaces**
- q/kind/total与GET单项元数据按契约§4；`AgentService.get_secret_metadata(access, secret_id) -> AgentSecretOut`（async）。
- useSecrets增加q/kind查询键与服务端条件；`useSecret(scope, secretId)`读选中项，不取值。useSecretAccesses传已有secret_id，结果与分页仍服务端过滤。
- secret_id通过URL定位，selected项不在当前页仍可GET。引用grants展示名称／ref_id，管理授权只链接相应实例，不把整个实例grants重写为单密钥。

- [ ] **Step 1 — 后端RED：** 超过50条数据，最后一项可被q/kind搜索到，total是全量匹配，游标换条件拒绝；owner可读／admin/member/viewer拒绝、跨空间404；详情从不含原值／密文／历史值。secret_id取用记录复用原接口，失败项不区分未授权与不存在。
- [ ] **Step 2 — 确认RED并实现读契约：** uv run pytest tests/test_secret_catalog_queries.py -q；完成scope＋过滤＋计数＋详情，静态/accesses在UUID前，grants批量转换无N+1；运行原密钥安全测试并生成类型。
- [ ] **Step 3 — 前端RED：** 秘密只输入不回显，切对象／关闭／提交清新值；轮换version与value_version区分，409不误报成功；删选中项清面板并提示授权影响。跨页搜索不只筛当前数据，deep link失权清旧内容。
- [ ] **Step 4 — 实现界面：** 列表／详情／取用记录、轮换／编辑／删除和可读授权；无存量眼睛／复制／有效期假字段。原类型与授权规则保留，source_ip仅原owner可见。
- [ ] **Step 5 — GREEN与交付：** npm run test -- tests/secret-catalog-redesign.test.ts及相关agents用例，后端读／轮换／安全回归通过；附元数据／无授权／轮换冲突截图、README质量门槛和真实输出。代码与自己的任务更新同PR。

## 六页统一交付

- [ ] 运行README中的前后端质量门槛与collab check；不用未运行的检查结果填PR。
- [ ] 角色矩阵、跨工作空间、dirty/409、外部服务失败、亮暗／窄宽／键盘有真实用例；不只截图正常态。
- [ ] 按实际T号和PR模板交付，写明图片的有意调整和无数据库变化；未完成保留进度。按D-0004处理审查与合并，不自动部署或推进Sprint。
