# 智能体服务器／OpenClaw 接口、状态与迁移约定

**本文是拟议第一方契约，需 Claude 实现后导出OpenAPI；不表示当前接口已经支持。**
与[设计规范](2026-11-09-agent-servers-openclaw-design.md)和[实施计划](../plans/2026-11-09-agent-servers-openclaw-redesign.md)一起阅读。路径均以`/api/v1`为前缀。

## 1. 通用约束

- 服务器／实例管理仍为owner权限，X-Workspace-Id、CSRF、Idempotency-Key和version沿用项目写接口约定。跨空间资源404，角色不符403，失权后清除前端缓存。
- GET不登录SSH、不安装／部署／重启；真实连接测试和后台操作都是显式POST。写幂等覆盖服务器保存、测试、初始化、凭证更新、新建部署及重试。
- 密码字段使用SecretStr，长度1–1024，不trim；错误响应（含422的input）、请求日志、脱敏任务输出均不能回显。幂等context／响应只放ID、状态与安全元数据，敏感请求指纹使用项目的安全摘要方案，不缓存请求原文。
- 新资源版本字段是服务器version；凭证revision是内部并发保护，两者不与实例version混用。改凭证／地址前先确认没有同机活动任务。
- 下文新增schema／方法名是跨任务接口；开工后若已有等价能力，沿用等价契约并在ADR列出映射，不重复造接口。

## 2. 数据模型：加法扩展

在既有`AgentSshTarget`／`agent_ssh_targets`添加：

| 字段 | 类型／缺省 | 作用 |
|---|---|---|
| login_password_ref | nullable text | CredentialBox密文，context=`agent_server_password:<target_id>`；只内部使用 |
| credential_revision | integer，默认0 | 设置／替换密码加1，加密主密钥轮换不加1 |
| last_connection_at | nullable timestamptz | 最近显式检测／真实SSH连接完成的时间 |
| last_connection_reason | nullable text | connected/auth_failed/unreachable/host_key_changed/credential_unreadable等白名单原因 |
| legacy_key_cleanup_state | text，默认not_needed | not_needed/pending/done/manual_required；有历史公钥或私钥的记录回填pending |

- 旧记录login_password_ref=null，不生成假密码；保留prepared_at／server_id／部署／归属。历史prepared_at保留环境就绪证据，credential_missing是另一维度。
- `client_key_ref/client_public_key`暂留迁移兼容列；新OpenClaw逻辑绝不读取其私钥来登录。成功退役后清空对应引用；本轮不DROP这些列。
- 轮换注册表加入login_password_ref及上述context，保留其他字段轮换。禁止用旧密钥context保存新密码，禁止把env引用当可回显密码。
- DDL升级／降级测试必须在临时库；降级不可恢复已主动退役的远端公钥或已清理的私钥，生产回滚前先导出结构／备份并评估版本兼容，不把降级当连接迁移的撤销按钮。

## 3. 服务器响应与状态

扩展`ServerSshOut`：
- `auth_method: Literal['password']='password'`
- `password_set: bool`、`credential_revision: int`
- `connection_status: Literal['unknown','connected','auth_failed','unreachable','host_key_changed','credential_unreadable','credentials_required']`
- `last_connection_at: datetime | null`
- `legacy_key_cleanup_state: Literal['not_needed','pending','done','manual_required']`
- 继续host/address/port/username、主机指纹与确认时间、prepared_at；不返回login_password_ref。
- OpenClaw服务器响应移除client_key_installed/client_key_fingerprint的依赖与UI；可作为deprecated迁移元数据短暂兼容，但不得参与prepared／deployable判断或被写成“密码已验证”。Runner的`AgentSshTargetOut`不跟随修改。

扩展`AgentServerOut`：
- `initialization_status: Literal['not_initialized','initializing','ready','failed','interrupted']`
- `deployable: bool`、`deploy_block_reason: str | null`
- `managed_deployments: int`：实际`AgentDeployment`记录数，包含失败待恢复／保留目录的部署，不能等同于成功数。另有`deployment_counts_partial: bool`标识历史安装证据不完整。
- `successful_deployments: int | null`：installed_at有值且runtime_installed=true的部署数，runtime_installed=false的保留目录记录不计；存在runtime_installed=null的旧部署时该总数为null、deployment_counts_partial=true，不能把未知当0。旧成功部署重部署失败时保留历史成功证据，详情同时标明最新任务失败。
- `allocated_mem_limit_mb: int | null`、`allocated_cpus: float | null`、`has_unlimited_limits: bool`：仍有部署记录的实例限额汇总，包括保留数据／停止实例；任一无限额时对应总上限为null，并明确含无限额，不代表实际占用。
- 已有instances_total／instances_online继续表示归属实例数及其既有在线口径；kind=auto同样保留，不能拿它们当平台部署成功数。

初始化状态从prepared_at和最新prepare任务推导，不单建第二套任务机：最新prepare running→initializing；failed／interrupted分别映射；最新成功或已有prepared_at且无更晚失败→ready；其余not_initialized。自动识别机器无SSH初始化状态时展示“不适用”，不计待初始化。

deployable要求：kind=ssh、password_set、主机指纹已确认、initialization_status=ready、无同机活动部署／运维、没有明确的凭证／身份失败。连接状态unknown或旧检测不是连通保证，实际部署仍做预检。GET只返回阻塞原因，不自动探测。

`AgentServerList`新增summary：`server_count`、`ready_count`、`successful_deployments`、`not_initialized_count`、`needs_credentials_count`、`deployment_counts_partial`、`generated_at`。successful_deployments同样为int|null，有旧未知记录时为null并提示待核验；其他计数为非负int。基于当前空间完整列表；ready_count表示环境ready，不等同于deployable或在线；待初始化只计ssh且not_initialized。自动识别与待补密码另列说明，数字不要求相加等于服务器数。

`ServerAgentRow`保持现有字段并补：`deployment_state:'associated'|'deploying'|'deployed'|'failed'|'interrupted'|'retained'`、`latest_job:AgentSshJobOut|null`、`deployment_project:str|null`。associated表示只有手动／指纹归属；deployed需实际部署成功证据；retained表示已卸载容器但保留数据。无真实容器检测结果不输出假container_status。

## 4. 服务器命令

| 接口 | 输入与返回 | 行为 |
|---|---|---|
| POST /agents/servers | 修改AgentServerCreate：name可选，host、port=22、username、login_password必填；201 AgentServerDetail | 探测主机身份后加密保存，不登录、不初始化。name省略生成host:port，名称上限沿用现有100字符 |
| POST /agents/servers/{id}/confirm | 复用AgentSshConfirm与当前version | 只确认已探测指纹；确认前不发登录密码 |
| POST /agents/servers/{id}/test-connection | 新AgentServerTestIn={version}；200 AgentServerDetail | 固定指纹＋密码登录，检查权限／环境；不安装、写公钥或启动容器。实际连通失败在ssh.connection_status中表达，权限／版本错误仍用HTTP错误 |
| PUT /agents/servers/{id}/credentials | 新AgentServerCredentialsIn={version,login_password}；200 AgentServerDetail | 替换加密密码、credential_revision+1、检查状态重置unknown；保留prepared_at与实例。之后显式测试连接 |
| PUT /agents/servers/{id}/address | 现有AgentServerAddress，扩展login_password必填 | 新地址／账号重新探测、取消身份确认并重置环境检查；有实际部署记录时返回409 AGENT_SERVER_HAS_DEPLOYMENTS，不能把原实例挂到新机器 |
| POST /agents/servers/{id}/prepare | AgentServerPrepare改为{version}；202 AgentServerDetail | 只读取保存密码，独立初始化，无authorize_key步骤 |
| POST /agents/servers/{id}/legacy-key-cleanup | 新AgentServerLegacyCleanupIn={version}；200 AgentServerDetail | 密码验证成功后精确清理本工作台历史公钥，成功再清空旧引用；不能用旧私钥。失败保留pending/manual_required及可理解原因 |
| POST /agents/servers/{id}/revoke | 旧OpenClaw密钥登录退役入口 | 返回410 AGENT_SSH_KEY_AUTH_RETIRED，指向补密码／迁移；不能悄悄用私钥做最后一次登录 |
| DELETE /agents/servers/{id} | 沿用version／既有参数约定 | 有部署／活动任务返回409；旧公钥未退役时提示先完成迁移，不顺手删容器或改sshd |

新增错误码至少包括：AGENT_SERVER_CREDENTIAL_REQUIRED、AGENT_SERVER_CREDENTIAL_UNREADABLE、AGENT_SERVER_NOT_INITIALIZED、AGENT_SSH_KEY_AUTH_RETIRED；原身份变化、任务运行、版本冲突与不可访问错误保留。公开输出只给原因类别与安全提示。

`AgentOpsState.available/reason`改为基于部署与密码可用性：保留not_deployed，新增credential_missing／credential_unreadable／host_key_unconfirmed；旧key_missing可deprecated兼容但不再作为就绪依据。二次认证的body.password仍是**工作台用户密码**，不能挪作SSH密码。

## 5. 一次点击创建与部署

### 5.1 新命令
`POST /agents/deployments`，owner，202；新`AgentDeploymentCreateIn`：
- request_id:UUID（同时作为Idempotency-Key，二者必须一致）
- server_id:UUID、server_version:int>=1
- name:1–64字符、description默认空且≤500、im_nickname可选且≤32
- cpus:float|null（0.1–256）、mem_limit_mb:int|null（256–1048576），缺省均null
- 不接受host、SSH密码、镜像URL、部署路径、network_mode或新增权限开关；自动设置network_mode=public、scope=read、max_role=member。

新`AgentDeploymentCreateOut={request_id:UUID,instance:AgentInstanceOut,deployment:AgentDeploymentState}`。

新`AgentDeploymentService.create(access, body, *, client_ip) -> AgentDeploymentCreateOut`：
1. 校验owner、workspace、服务器版本／就绪／无忙任务及现有全局开关、平台接入入口前置条件；不满足时不创建实例。
2. 复用AgentService的建模／审计和限额校验；在同一业务事务中落实例、资源设置、AgentDeployment、AgentSshJob和幂等context中的instance_id/job_id，再提交。
3. 提交成功后启动既有runner；IM注册沿用既有可重试机制，不把外部HTTP放进数据库原子性承诺中。启动异常按既有恢复机制处理，不能创建第二个实例来“补偿”。
4. 幂等返回同一instance/job；任务后续失败仍保留该实例，可用现有deploy接口继续。新建命令重放不重启失败任务。

### 5.2 查询收据与重试
新增`GET /agents/deployments/requests/{request_id}`，owner且必须原actor／workspace匹配：
`AgentDeploymentRequestOut={request_id:UUID,state:'processing'|'accepted'|'failed'|'interrupted',instance:AgentInstanceOut|null,deployment:AgentDeploymentState|null,error_code:str|null}`。
- 从现有幂等记录的安全context解析资源ID，再读取当前实例／部署；不返回原请求体、响应原文或任意其他动作收据。
- processing与后台job.running不是同一含义：accepted表示命令接纳，实际任务进度仍读deployment.job。
- 前端仅短期保留request_id／实例ID和所属用户空间，不保存服务器密码。WRITE_UNKNOWN、刷新或断网恢复先GET收据；找不到／记录过期时提示查询现有实例，不自动换新request_id重建。
- 对同一请求号不同body返回现有幂等冲突；并发同键只产生一次实例和任务。同一服务器不同键并发，由服务器互斥保证另一请求409且不留多余实例。
- 业务事务提交前崩溃无半个实例；已提交但还未回响应时可从context定位唯一资源；recover对悬挂processing和running按现有时间规则清理／中断，不自动重新创建。

现有`POST /agents/instances/{id}/deploy`保留，输入仅server_id和实例version，移除一次性SSH password语义；本期客户端不传password，旧password字段若暂留兼容必须不使用且不回显。同机重复部署／重试复用该实例，跨服务器部署仍409。卸载保留原remove_files规则。

## 6. 智能体只读总览

新增`GET /agents/overview`，owner：server_id可选、q为实例名称（1–100字符）、status采用原InstanceStatus、cursor／limit沿用既有分页约定。
`AgentOverviewOut={summary,items:list[AgentOverviewRow],next_cursor:str|null,generated_at:datetime}`。
- summary：`total,online,offline,pending,disabled,deploying,attention`，均为非负int。全量统计限定server_id/q；不套用status筛选，不受分页影响。online等遵循原实例状态，deploying为install job.running，attention为部署failed/interrupted或非disabled的offline实例去重并集。
- `AgentOverviewRow={instance:AgentInstanceOut,server:AgentServerOut|null,deployment:AgentDeploymentOut|null,latest_job:AgentSshJobOut|null,deployment_project:str|null}`。
- 批量读取本地实例、部署、归属与任务；不为每行发SSH／OpenIM请求，不解析原始日志猜状态。部署来源优先实际AgentDeployment，之后现有手动／指纹归属；未归属明确显示。
- `AgentDeploymentOut`增加`runtime_installed:bool|null`：新部署成功写true，成功卸载容器且保留目录写false，运行时未知旧记录为null。用于区分retained，不代表此刻Docker运行中。旧null只能通过显式核验对应受控项目／容器是否存在补证据，GET不得自动SSH核验。
- 若当前AgentDeployment缺少“容器已卸载但保留目录”标志，在同一加法迁移中补nullable runtime_installed列；不从文件路径存在或installed_at非空反推当前运行。
- 最近心跳显示last_seen_at，消息在线按现有status，容器运行状态按真实检测，三者独立。

## 7. 事务与共享代码边界

- 既有AgentService.create_instance、AgentServerService._start_job包含commit；统一入口应提取无独立commit的内部建模／接纳函数，保留旧公开接口行为。不能把几个已有会commit的方法串起来宣称一个事务。
- SshJobRunner.reserve／commit／launch顺序和数据库互斥索引维持，事务失败释放reserve。prepare／install／uninstall／运维互斥保持。
- OpenClaw的password-only策略单独命名；RunnerInstaller继承的通用方法保留兼容。禁止全局删掉authorize_key或client_key参数导致Runner坏掉。
- 密码更新、地址修改、删服务器和旧公钥清理也要获取同机操作锁／busy检查；多API进程并发须使用现有数据库／分布式锁规则，不能只靠浏览器禁用或进程内flag。
- 原SSH ADR中“一次密码后使用专用密钥”由新ADR在OpenClaw服务器范围取代；历史文档不抹除，更新当前部署手册与威胁模型。
