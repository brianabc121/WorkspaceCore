# 智能体服务器／OpenClaw 智能体 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 本包由人类交给Claude实施；当前Codex仅写方案与打包。Claude环境无上述技能时按等价流程执行，不为读取文档安装插件。

**Goal:** 将服务器管理与OpenClaw智能体管理拆成两页，落实密码连接、独立初始化、一机多智能体自动部署及双向可见。

**Architecture:** 复用现有AgentServer／AgentDeployment／AgentSshJob和多实例隔离机制，单独引入OpenClaw服务器的password-only连接策略。先加法扩展凭证与迁移契约，再改初始化／部署／运维，提供事务一致的新建部署命令和本地总览，最后落两页UI与真实SSH／Docker验收。

**Tech Stack:** Vue 3、TypeScript、Vite、Pinia、TanStack Vue Query、现有Tailwind/shadcn-vue/reka-ui与Lucide；FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL、Redis、Alembic、CredentialBox、已锁定asyncssh、Docker Compose。前端Node 22和npm ci。

**Spec:** [产品／设计规范](../specs/2026-11-09-agent-servers-openclaw-design.md)与[接口／迁移约定](../specs/2026-11-09-agent-servers-openclaw-contract.md)，必须同时阅读。
**核对基线:** origin/main `4997fc3d61ee2add09541cc48e4fcba6ae9f7d49`，含首页#177及第一批四页#178。开工重新fetch；前端路由真实位置是`apps/web/src/app/router.ts`，不是旧根级router文件。

## Global Constraints

- 两个独立入口：`/agent-servers`智能体服务器、`/agents`OpenClaw智能体。`/runner`与`/agents/secrets`保持各自功能。
- 管理页／新接口沿用owner权限，workspace隔离、CSRF、幂等、乐观版本和审计保持；原运维admin能力及二次认证不扩大也不移除。
- 只取消OpenClaw服务器的SSH客户端密钥认证；主机指纹、工作台加密密钥、mTLS、实例令牌、密钥库授权及Runner的SSH流程保留。
- 密码只写不读、加密存储，明文不进URL、日志、队列、SSE、幂等缓存、前端持久化、接入包或智能体容器。
- 旧服务器无密码可还原，需补录；已有实例继续运行，不强制重装、不换UUID、不清目录。DDL仅加法扩展，远端旧公钥按对象显式退役。
- 同机副作用任务互斥；一机多实例复用独立Compose项目／目录／网络／凭证和既有net持有网络命名空间机制。
- 默认限额仍为null；容量不足沿用D4提醒，不悄悄改为固定2核／4GB或新增全局硬上限。
- 不向OpenClaw开放宿主exec／Docker socket／host网络／privileged；不自动改sshd，不引入镜像商城或通用SSH终端。
- 所有统计来自服务器全量投影；消息连接、心跳、部署任务、容器运行状态分别表达。未知不是0，断线不是容器已停止。
- 生成OpenAPI和schema.d.ts必须由后端契约机械产生，不手改，不从旧T-0149分支复制生成输出。
- 只实施本轮范围，不覆盖已合并首页和第一批四页，不改他人的占用路径，不恢复n8n，不在本次Codex会话开发或部署。

## Review Focus

1. 旧SSH服务器只有私钥／公钥、密码缺失或主密钥轮换：已运行实例不停，新SSH动作不回退私钥（Task 1/2）。
2. 凭证更新与同机任务并发、指纹变化、SSH成功但sudo失败：不连错主机、不误报初始化完成（Task 1/2）。
3. 两次点击、同键不同body、提交前后进程崩溃／响应丢失：唯一实例与任务可查询恢复（Task 3/5）。
4. 同机两个实例重试／卸载一个、准备环境与Runner共享代码：另一个实例和Runner均保持可用（Task 2/3/6）。
5. 旧深链接、自定义导航、长IPv6、无指标、在线不等于容器运行：路由与权限正确，未知值不被伪造（Task 3/4/5）。

---

## 0. 开工与任务拆分

- [ ] 读最新AGENTS.md/docs/AGENTS.md、.collab任务／交接、D-0003目录归属、D-0004合并规则、Sprint批准记录；确认本轮人类指派及是否已有重复执行／PR。
- [ ] `git fetch origin main`后运行`python3 scripts/collab.py inbox --as claude`，在安全检出中从最新主干开自己的分支。保护所有未提交工作，不reset／clean其他执行。
- [ ] 读取OPENCLAW_PLAN、MULTI_OPENCLAW_PLAN、AGENT_OPS_PLAN、SSH provisioning与multi-openclaw verification ADR、credentials轮换手册和OpenAPI。
- [ ] 按现场可用T编号建立下面六项任务并声明paths，接手后start；Task编号仅计划顺序，不是预定T编号。人类明确交给Claude的指派按现有协议记录，不替别人改owner。
- [ ] 将本包docs／assets保留相对路径。新ADR记录“本次用户要求取代旧OpenClaw专用SSH密钥方案”，不改协作协议，不要求为已明确授权的密码方式重复确认。
- [ ] 顺序Task 1→2→3→4→5→6，共享API/schema／导航／生成文件串行修改。默认同一交付分支六个可审查提交，整包一个主任务PR；接口有破坏性变更，Task 1–5未配套完成且Task 6未验收前不合并。若实施者选择拆PR，必须另做兼容过渡并逐PR证明旧页面仍可用，不能只先合新必填字段。

## Task 1：加密密码、服务器连接契约与加法迁移

**Files**
- Modify: `services/api/app/models/agent.py`、`app/schemas/agents.py`、`app/api/v1/agents.py`、`app/domain/agent_servers.py`
- Create: `services/api/app/domain/agent_server_credentials.py`
- Modify: `services/api/app/domain/credential_rotation.py`
- Create: `services/api/migrations/versions/<最新主干下一revision>_agent_server_passwords.py`
- Create: `services/api/tests/test_agent_server_credentials.py`、`test_agent_server_password_migration.py`
- Regress: `services/api/tests/test_agent_servers_api.py`、`test_credentials.py`、`test_credential_rotation.py`
- Create: `docs/decisions/2026-11-09-agent-server-password-auth.md`（开工若同名已占用，用下一实际日期／唯一名称）
- Generate: `services/api/openapi.json`、`apps/web/src/services/schema.d.ts`

**Interfaces**
- 按契约§2添加AgentSshTarget密码引用、revision、检测状态、旧公钥退役状态；AgentDeployment补nullable `runtime_installed`，旧记录保持null。
- `agent_server_password_context(target_id: UUID) -> str`返回`agent_server_password:<uuid>`。
- `resolve_server_password(box: CredentialBox, target: AgentSshTarget, *, expected_revision: int | None = None) -> SecretStr`：缺失／解密错误／revision变化抛稳定领域错误，不返回ref到API。
- `AgentServerService.set_credentials(access, server_id, body: AgentServerCredentialsIn, *, client_ip) -> AgentServerDetail`；`test_connection(access, server_id, body: AgentServerTestIn, *, client_ip) -> AgentServerDetail`。
- 创建加入login_password；新增PUT credentials、POST test-connection；保留主机身份confirm。ServerSshOut新增password_set／connection_status等，详情新状态按契约§3；本项暂不接入新的部署入口。

- [ ] **Step 1 — RED密码用例：** `test_create_stores_sealed_password_and_never_returns_it`检查DB为enc引用、输出无明文／密文ref，密码前后空格不丢；`test_unconfirmed_host_never_receives_password`；`test_wrong_workspace_and_non_owner_cannot_write`；`test_validation_and_idempotency_records_redact_secret`包括422 input／日志／SSE。更新密码version过期409，活动任务时409且原密码不变。
- [ ] **Step 2 — RED迁移／轮换：** 旧SSH服务器无密码且指纹／prepared_at／部署／实例ID保持；历史key_cleanup回填pending；自动识别服务器不冒充可部署。密码主密钥轮换后可解密、credential_revision不变；把密文挪到另一目标解密失败。runtime_installed旧数据未知，不凭installed_at填true。
- [ ] **Step 3 — 确认RED：** 在services/api跑`uv run pytest tests/test_agent_server_credentials.py tests/test_agent_server_password_migration.py -q`；失败应来自未实现字段／契约，不是环境未启动。记录真实输出。
- [ ] **Step 4 — 实现存储／连接检测：** 用CredentialBox写密码并注册轮换；创建只探测身份后保存，确认后显式检测密码。检测不得安装Docker／创建容器／安装公钥。更新密码保留环境就绪；换地址有部署时拒绝。按当前锁规则保护并发和revision，不仅依赖前端busy。
- [ ] **Step 5 — 迁移与GREEN：** 临时库升级→旧数据断言→降级→再升级；旧私钥不在DDL中清空。跑新测试及现有凭证／服务器测试，导出OpenAPI／生成类型。若新旧契约需过渡兼容，写清deprecated元数据，禁止把旧key字段当password_ready。
- [ ] **Step 6 — 提交：** 代码、迁移、ADR、契约、生成类型、实际验证和自己的任务记录作为交付分支的一项提交；使用实际T编号。此阶段可审查，但不单独合并破坏性接口，不宣称所有旧密钥路径已改完。

## Task 2：初始化、卸载和运维统一密码认证

**Depends on:** Task 1。
**Files**
- Modify: `services/api/app/domain/agent_servers.py`、`agent_ssh_install.py`、`agent_ops.py`、`agent_ssh.py`
- Create: `services/api/app/domain/agent_server_ssh.py`（OpenClaw专用认证策略）
- Modify: `services/api/app/agent_provisioning/__init__.py`、必要的`files/inspect.sh`／`install-docker.sh`
- Modify: `services/api/app/schemas/agents.py`、`app/api/v1/agents.py`
- Test: `services/api/tests/test_agent_server_password_jobs.py`（新）、`test_agent_server_legacy_cleanup.py`（新）
- Regress: `test_agent_ssh.py`、`test_agent_ssh_install.py`、`test_agent_ops_api.py`、`test_runner_ssh.py`
- Generate: OpenAPI与前端类型；更新Task 1的ADR说明最终切换边界。

**Interfaces**
- `connect_agent_server(target: AgentSshTarget, box: CredentialBox, *, expected_revision: int) -> SSHClientConnection`（async）：只密码、固定主机指纹，禁止ssh-agent／默认私钥／publickey回退。
- `AgentServerPrepare={version}`，AgentDeployIn不再依赖一次性SSH密码；OpenClaw的prepare／install／uninstall与AgentOpsService都通过上面策略读取保存密码。
- prepare步骤使用connect／inspect／docker／reconnect及新增`workspace`／`verify`，无authorize_key；Runner仍可使用原authorize_key步骤和策略。
- `AgentServerService.cleanup_legacy_key(access, server_id, body: AgentServerLegacyCleanupIn, *, client_ip) -> AgentServerDetail`，新增端点只用密码；旧OpenClawrevoke端点410。准备状态与OpsState.reason按契约，不再要求client_key_ref。
- 执行进程临登录时读取保存密码与revision；连接信息改变被拒绝／版本变动则中止。后台任务记录不放密码或可回显密文。

- [ ] **Step 1 — RED认证：** 新测试覆盖prepare、deploy、undeploy、restart/up/status/logs/prune等所有现有OpenClaw SSH动作；用spy断言client_key始终空、authorize_key未调用，即使旧private ref存在也不读取。旧记录无密码明确credential_required，不影响已运行智能体API。
- [ ] **Step 2 — RED初始化：** 服务器已有Docker时无安装／升级／全局prune；缺Docker才按锁定脚本安装；sudo失败／不支持系统／主机指纹变化／加入docker组后密码重连失败都在正确步骤失败。重复prepare不创建实例、接入包、容器或新公钥。
- [ ] **Step 3 — RED退役与隔离：** 密码登录清理精确旧公钥，authorized_keys内其他用户和Runner公钥保持；远端失败不能标done，旧私钥不作回退。RunnerInstaller原流程仍可准备／部署／卸载；运维body.password仍校验工作台账号二次认证而非SSH密码。
- [ ] **Step 4 — 确认RED：** `uv run pytest tests/test_agent_server_password_jobs.py tests/test_agent_server_legacy_cleanup.py -q`，定位行为失败后实现。
- [ ] **Step 5 — 实现：** 为OpenClaw单独选择password策略；Installer共享命令／超时／脱敏可复用，Runner覆盖点保留，不全局删authorize。初始化只准备基础环境与受控目录，成功记录prepared_at；卸载／运维改密码路径。显式旧公钥退役成功再清旧ref，失败给pending/manual_required。password通过stdin给sudo，断开重连仍用保存密码。
- [ ] **Step 6 — GREEN：** 新旧SSH、运维和Runner测试通过；对日志、错误、审计与runner启动参数做真实断言，防止遗留分支带密钥。API重启按现有规则把running标interrupted，不假装已完成或无限自动重试。
- [ ] **Step 7 — 提交：** 完整认证路径、更新契约与测试同一阶段提交，说明旧服务器补密码、退役步骤及已有实例保持运行。不要把仅删除UI按钮称为已完成密码改造。

## Task 3：幂等部署入口、实例总览与可靠归属

**Depends on:** Task 2。
**Files**
- Create: `services/api/app/domain/agent_deployments.py`、`agent_overview.py`
- Modify: `services/api/app/domain/agents.py`、`agent_servers.py`（提取无独立commit的内部函数）、`agent_ssh_install.py`（成功／卸载标志）
- Modify: `services/api/app/api/v1/agents.py`、`app/api/deps.py`、`app/schemas/agents.py`
- Reuse: `app/domain/idempotency.py`及现有记录；只在确有共用需求时修改，不改变其他API幂等语义。
- Test: `services/api/tests/test_agent_deployment_requests.py`、`test_agent_overview_api.py`（新）
- Regress: `test_agent_servers_api.py`、`test_agents_api.py`、`test_host_metrics_api.py`
- Generate: OpenAPI与前端类型。

**Interfaces**
- `AgentDeploymentService.create(access, body: AgentDeploymentCreateIn, *, client_ip) -> AgentDeploymentCreateOut`、`receipt(access, request_id: UUID) -> AgentDeploymentRequestOut`，均async；schema严格使用契约§5。
- `AgentService.create_instance_record(access, body: AgentInstanceCreate) -> AgentInstance`（async内部方法）只建模／审计／flush，不commit或外部IM调用；原create_instance经它维持已有公开行为。
- 从AgentServerService._start_job提取`accept_deployment(access, instance: AgentInstance, server: AgentServer, *, client_ip) -> AgentSshJob`（async内部方法）：创建部署／任务并reserve，不独立commit／launch；统一入口负责一处commit后launch。旧deploy接口复用相同接纳逻辑。
- `AgentOverviewService.list(access, *, server_id: UUID | None, q: str | None, status: InstanceStatus | None, cursor: str | None, limit: int) -> AgentOverviewOut`（async），schema按契约§6。
- 服务器list/detail新增真实summary、初始化／就绪／阻塞原因、逐实例部署状态；runtime_installed只在真实部署／卸载成功时更新，不把历史空值转成已运行。

- [ ] **Step 1 — RED唯一性：** 同request_id同body并发只创建一个instance／deployment／job；不同body同key冲突；同服务器不同request并发一项成功、另一项busy且无多余实例。server_version过期、无密码、未初始化、入口缺失、非owner／跨workspace均不留半个实例。
- [ ] **Step 2 — RED恢复：** 事务commit前故障没有孤立实例，commit后response丢失可通过收据定位相同ID；进程在launch前中断可recover，任务状态真实。新建命令重放不启动第二个job，失败后现有实例deploy重试不新建实例。幂等context无密码／原请求体，收据不能读其他actor／空间／动作。
- [ ] **Step 3 — RED总览：** 大于一页、自动归属、手动指定、无归属、失败／retained／legacy未知均有正确统计；total不等第一页，attention按并集去重，旧runtime_installed未知时部署成功数为null且partial，不能补0。offline或disabled不推出容器stopped，最后心跳只读last_seen_at。GET调用外部SSH／OpenIM次数为0，避免每行N+1查询。
- [ ] **Step 4 — 确认RED：** `uv run pytest tests/test_agent_deployment_requests.py tests/test_agent_overview_api.py -q`，保持与新契约直接对应的失败。
- [ ] **Step 5 — 实现部署协调：** 复用建模／限额／接纳逻辑，一处业务commit连同安全幂等context资源ID；外部IM可重试，SSH在提交后执行。内部方法不能偷偷commit。遵守reserve失败释放、服务器／实例锁顺序和现有跨API互斥，事务失败不误释放其他请求的锁。
- [ ] **Step 6 — 实现读侧：** 批量关联实例、部署、服务器和最新任务，遵循实际部署优先／手动归属／指纹规则；生成只读总览与summary。成功后写runtime_installed=true；卸载保留目录写false；失败不覆盖前次真实安装证据，详情同时显示最新失败。
- [ ] **Step 7 — GREEN与提交：** 新测试及agents／servers／host_metrics回归通过；生成契约，检查字段／nullable／默认值、GET静态路径优先于UUID路径。提交到同一交付分支，为两页UI提供完整可用后端；整体配套后再开合入PR。

## Task 4：智能体服务器独立页面与旧链接兼容

**Depends on:** Task 3。
**Files**
- Create: `apps/web/src/pages/AgentServersPage.vue`
- Modify: `apps/web/src/app/navigation.ts`、`navIcons.ts`、`router.ts`、必要的`src/App.vue`页面宽容器声明
- Modify: `apps/web/src/features/agents/ServersWorkspace.vue`、`ServerAdd.vue`、`ServerDetail.vue`、`ServerSsh.vue`、`SshJob.vue`、`servers.ts`
- Create: `apps/web/src/features/agents/ServerConnectionForm.vue`、`ServerInitializationPanel.vue`、`ServerAgentsTable.vue`、`agent-servers.css`
- Modify: `apps/web/src/pages/AgentsPage.vue`（本项先移除服务器标签，保留现有实例内容）及已有`DeployPanel.vue`等旧服务器链接
- Test: `apps/web/tests/agent-server-redesign.test.ts`（新）、`agents-servers.test.ts`、`navigation.test.ts`

**Interfaces**
- 新`/agent-servers`核心nav entry minRole=owner，图标Server；`/agents`标题改OpenClaw智能体、minRole不变。
- `servers.ts`保留现有serversKey／serverKey／deploymentKey的用户＋工作空间隔离；prepared／deployable改用生成状态，去掉client_key_installed条件。
- `ServerConnectionForm`接收`server?:AgentServerOut,busy:boolean,error:string`，发出create／credentials更新事件及close；login_password仅组件内存，提交／关闭／切服务器立即清空。
- `ServerInitializationPanel`接收ServerDetail与busy，发出confirm／test／prepare／legacy-cleanup操作；不在mounted／刷新／切范围时写远端。
- `/agents?tab=servers`转换为`/agent-servers`保留server／range；普通`/agents`留原实例组件，后续Task 5单独重排。所有跨页链接采用新路径，/agents/secrets不变。

- [ ] **Step 1 — RED页面：** 导航确有两个独立入口、旧tab链接replace到正确详情、默认与自定义导航可发现新入口，非owner不能打开；长IPv6／超长名称在390px不横向溢出。旧服务器无密码显示补录、auto记录不显示可部署按钮。
- [ ] **Step 2 — RED交互：** 仅四项必填可创建，名称可省略；密码不进localStorage/sessionStorage、URL、query cache、错误input。未确认身份时不能测试登录；更新密码不清已有初始化；测试不触发prepare，普通刷新POST次数为0。初始化／退役失败显示实际步骤而非全绿。
- [ ] **Step 3 — 确认RED：** `npm run test -- tests/agent-server-redesign.test.ts`，确认新路由、表单与状态尚未实现导致失败。
- [ ] **Step 4 — 实现导航／表单：** 建独立页面和专属宽布局，整合现有服务器组件；首连身份确认继续走既有指纹流程。使用最新server.version逐步执行create／confirm／test，不拿初始version连续发送。密码只在服务器页维护，移除专用密钥准备／吊销入口，旧记录展示迁移操作。
- [ ] **Step 5 — 实现总览／详情：** 采用完整summary与真实CPU／内存；初始化状态、连接状态、指标过期分别显示。详情按ID列出所有已部署／关联实例、来源、限额、最新任务与跳转；没有整机指标不显示0。图中部署清单与资源汇总优先，长细节进抽屉。
- [ ] **Step 6 — GREEN与提交：** 新测试、agents-servers及navigation通过；390/768/1280/1440/1920、亮暗、键盘／焦点归还、断网与跨空间迟到响应验证。仅更新本轮导航文案／链接，不改用户自定义标题或其他页面布局。

## Task 5：OpenClaw 智能体列表与部署抽屉

**Depends on:** Task 3／4。
**Files**
- Modify: `apps/web/src/pages/AgentsPage.vue`、`src/features/agents/AgentsWorkspace.vue`、`InstanceCard.vue`、`DeployPanel.vue`、`queries.ts`、`servers.ts`
- Create: `apps/web/src/features/agents/AgentOverviewTable.vue`、`AgentDeployDrawer.vue`、`AgentDetailPanel.vue`、`deploymentRequests.ts`、`agents-pages.css`
- Reuse: `InstanceForm.vue`、`LimitsPanel.vue`、`OpsPanel.vue`、唤醒／发件箱／工具权限等现有组件
- Test: `apps/web/tests/agent-deployment-flow.test.ts`（新）、`agents.test.ts`、`agents-host.test.ts`、`agents-ops.test.ts`

**Interfaces**
- `useAgentOverview(scope, filters)`消费AgentOverviewOut；filters包含server_id/q/status/cursor/limit，查询键仍以agents、user、workspace开头。
- `deploymentRequests.ts`提供`startAgentDeployment(scope: string, body: AgentDeploymentCreateIn) -> Promise<AgentDeploymentCreateOut>`、`getAgentDeploymentReceipt(scope: string, requestId: string) -> Promise<AgentDeploymentRequestOut>`（通过现有auth.api）；用request_id设置Idempotency-Key。
- `AgentDeployDrawer`接收可部署服务器、预选serverId、提交状态，发出submit；无SSH密码／IP／账号输入。仅选择服务器与实例设置。
- 可在sessionStorage保存安全恢复记录`{request_id,instance_id?,user_id,workspace_id}`，不存表单原文、密码或接入包。登出／换用户清除并停止请求；收据过期时先查询现有实例，不重建。
- `?deploy=1&server_id=...`打开抽屉且预选该服务器；不可部署时明确原因与去服务器页链接，不偷选第一台。`?instance=...`定位详情，未找到／失权时清空旧详情。

- [ ] **Step 1 — RED展示：** 总数大于一页仍按summary；server选择变更／旧响应延迟不串详情；pending/online/offline/disabled与任务进度分别呈现，未提供Docker状态时无“已停止容器”断言。实际部署优先归属，点击所属服务器可直达对应详情。
- [ ] **Step 2 — RED部署：** 准备两台ready、一台未初始化、一台待补密码；只有可部署的能选，来自服务器页的预选保留，非法server_id不自动回退。默认限额null，手动2核／4096MiB准确传递；无入口／总开关关按服务器返回错误，不造实例上线提示。
- [ ] **Step 3 — RED恢复：** 连点提交保持同request_id；POST响应丢失先GET收据，返回同实例／job；页面刷新恢复查询，不生成新请求号。接纳后的failed／interrupted提供继续原实例，原创建命令不反复执行；收据找不到时显示未知与查现有实例入口。缓存和持久化中无密码。
- [ ] **Step 4 — 确认RED：** `npm run test -- tests/agent-deployment-flow.test.ts`，保留真实失败后实施。
- [ ] **Step 5 — 实现：** 主表＋统计＋筛选＋详情按图布局；部署抽屉消费统一POST，展示环境检查、上传／镜像／容器／接入／等待上线真实步骤。成功／失败都刷新实例、服务器、deployment和overview查询键；部署中沿现有2秒轮询，页面隐藏／失权／任务结束按既有策略停止。
- [ ] **Step 6 — 保留功能：** 手动登记／接入包／intranet继续可达；权限开关、密钥授权、停用与容器运维保留真实区分。旧DeployPanel继续部署已有实例但不索要SSH密码，不在实例页创建／初始化服务器。工作台账号二次认证只在原敏感运维动作出现。
- [ ] **Step 7 — GREEN与提交：** 新旧agents／host／ops测试及截图通过；不硬编码5个实例、容器名、消息状态、镜像版本或资源容量。提交两页联动与真实失败／恢复截图，说明效果图文案的有意调整。

## Task 6：真实SSH／Docker验收、文档与交付闭环

**Depends on:** Task 1–5。
**Files**
- Modify: `tests/e2e/agent_ssh_acceptance.py`、`tests/e2e/agent_ssh_browser.cjs`、`.github/workflows/ci-infra.yml`
- Modify: `docs/v2/MULTI_OPENCLAW_PLAN.md`、`docs/v2/OPENCLAW_PLAN.md`、`docs/api-contract.md`、`docs/data-dictionary.md`、`docs/threat-model.md`、`docs/deployment.md`／`docs/deploy-guide.md`的相关当前章节
- Create: `docs/verification/<实际任务号>-agent-server-passwords.md`
- Regress: 现有Runner浏览器／SSH／多实例接入验收，不改它们认证需求。

**Interfaces / acceptance**
- 沿用ci-infra agents job的真实sshd、sudo、锁定Docker、OpenClaw与接入入口准备；修改旧“一次密码后密钥登录”的验收期望，不删掉真实SSH测试来凑通过。
- 部署两个不同instance_id到同一个server_id，分别验证独立项目、网络命名空间、目录、容器资源限制、平台连接与服务器页归属。

- [ ] **Step 1 — 改验收用例：** 密码入库不可回显；断言sshd认证为password/受限keyboard-interactive且新authorized_keys未增加平台密钥；原环境安装成功且无智能体，之后分别部署甲乙。每实例自己的mTLS／令牌仍有效，SSH密码不在包／容器／输出中。
- [ ] **Step 2 — 改恢复／迁移用例：** 网络断开后按收据继续，实例数不变；旧key-only服务器上已有实例继续运行，补密码后用密码定向移除旧key，其余authorized_keys不变。密码错误、主机指纹改变、API重启、部署失败／重试与同机busy能恢复到真实状态。
- [ ] **Step 3 — 跑真实多实例：** 甲乙均接入，重部署甲保持乙容器ID／目录／任务；卸载甲保留数据后runtime_installed=false，乙仍接入；甲删目录时只删甲。乙仍有部署记录则拒绝移除服务器。清完部署后可删除空服务器，Runner原SSH验收继续通过。
- [ ] **Step 4 — 浏览器：** 走两侧栏页、仅四项连接表单、指纹确认、初始化进度、预选服务器部署、服务器详情看甲乙、旧链接及手机宽度。覆盖亮暗、键盘、失权、无数据／stale；首屏展示真实统计，截图标测试数据。
- [ ] **Step 5 — 更新当前文档：** 新ADR链接取代旧OpenClaw密钥流程的部分；历史ADR保留。记录密码加密轮换、补录与定向清理、恢复／回滚边界、真实API、迁移字段和运维二次认证区别。不把Runner／mTLS／全局密钥库文档删除。
- [ ] **Step 6 — 完成：** 执行下述质量门槛与协作check，保存真实命令、环境版本、退出码与失败原因。无法运行真实SSH／Docker时如实blocked，不将本轮整体验收标done，不用mock代替一机两个真实实例证据。

## 7. 命令、PR与完成标准

以下命令是**Claude实施阶段执行清单**；本包没有执行产品开发测试。

前端在`apps/web`，先选择Node 22：

```bash
node --version
npm ci
npm run lint
npm run typecheck
npm run test
npm run check:api
npm run build
```

后端在`services/api`，使用仓库测试数据库／Redis：

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests scripts
uv run python -m scripts.export_openapi
uv run python -m scripts.export_openapi --check
uv run pytest
```

契约更新先export，再在前端`npm run generate:api`，审阅生成差异并纳入提交。check:api通过git diff验证零漂移，应在生成文件同步到索引／提交后运行，禁止手改输出。

迁移仅在临时测试库验证（不是生产）：

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

真实SSH／Docker／浏览器在仓库根、**完成ci-infra agents job既有测试环境准备后**执行当前同等命令：

```bash
sudo -E python3 tests/e2e/agent_ssh_acceptance.py
node tests/e2e/agent_ssh_browser.cjs
```

不在真实用户服务器上跑卸载／清理测试；CI中的SSH密码从测试配置提供，不出现在报告。若脚本依赖参数在新主干变化，采用当前CI同等命令并记录偏差，不能只运行空壳脚本称通过。

仓库根：

```bash
python3 scripts/collab.py check
git diff --check
```

- [ ] 六项任务分别更新实际验收状态，未完成记录真实进度、已验证结果和下一步，不虚标done。
- [ ] 按`.github/pull_request_template.md`、`[T-主任务实际编号] 简述`创建整体PR，列出六阶段关联任务，代码与任务更新同PR；附命令输出、迁移影响、两页截图、旧服务器迁移说明和效果图偏差。
- [ ] 保证首页#177、第一批四页#178、模块宿主、全局搜索、Runner、消息和既有智能体工具不回归。
- [ ] 按最新D-0004审查／合并；部署、不可逆迁移、改人类文件／协作协议、推进Sprint继续按仓库规则，不由本包自动执行。

完成定义：两页可用，密码连接覆盖全部OpenClaw SSH操作，旧实例可迁移且不重装，同机两实例真实验收通过，失败可恢复不重复创建，统计与状态真实，文档与契约一致。
