# AI审批与管理区：接口补充、状态与兼容约定

**以下新增能力为拟议第一方契约，尚未开发。** 与[页面规范](2026-11-09-ai-admin-design.md)及三个实施计划配套阅读。保留原URL／参数和默认行为，契约变化机械导出OpenAPI与schema.d.ts。

## 1. 统一边界

- 账户资料／会话按当前用户，其他接口按X-Workspace-Id；新功能不扩大原角色。对象不存在／跨空间遵守现有404，不用前端隐藏代替后端验证。
- 所有写操作保持原CSRF、version/If-Match及幂等语义。新汇总／搜索／详情不批准、发邮件、触发同步或生成早报。
- 敏感值只写不读；后端422详情、日志、审计与幂等记录不得包含密码／Token／Webhook原值。公开回调地址、ID可复制。
- 新列表条件进入服务端过滤并在limit前生效。筛选变化重置cursor，旧页不拼进新条件；游标不能作为授权凭据。
- 无值与未知为null或对应未知态，不补0。读取完整范围的统计必须SQL聚合，不靠翻完前端页计数。
- 本期复用现有表，目标不新增业务迁移；仅确有必要的索引变更可附加法迁移与验证。

## 2. AI审批读侧

### 2.1 新增汇总

新增`GET /api/v1/ai/tool-executions/summary`，viewer可读，使用AiToolService现有_visible(access)。静态summary路由必须早于`/{execution_id}`。

参数：
- period: `24h | 7d | all`，API缺省all，UI默认24h；按created_at筛选“此期间发起的调用”，由服务端UTC现在计算范围。
- instance_id、risk_level（复用现有多值RiskLevel）、tool_name与列表相同。
- 不接收status：卡片是所选非状态条件下的全量状态概况，不随待审批／异常标签切换而变成同一个计数。

新增`AiExecutionSummaryOut`：
- pending_count:int：pending_approval且expires_at>as_of。
- expiring_count:int：pending_count中expires_at<=as_of+180秒。
- critical_pending_count:int：有效pending中risk_level=critical。
- exception_count:int：当前period发起、状态failed或unknown的记录数。界面标“最近24小时发起的执行异常”等实际范围；rejected/expired/denied不计执行失败。
- as_of:datetime、period、expiring_window_seconds:int=180。
- instances:list[ExecutionInstanceOption]，`{id:UUID,name:str}`；tools:list[str]。均从当前用户可见、period内调用记录取得，生成可用筛选项，不调用owner专属实例列表来服务member/viewer。

统计与筛选项分别计算：统计应用所选instance/risk/tool；筛选项基于_visible＋period，不被其他已选条件锁死。实例选项只返回仍有ID的记录，名称取该ID最近记录名；已删实例仍在“全部”结果中保留历史名称，不把null映射到别的实例。

成员／访客只计算自己的可见调用；不沿用旧pending_count全空间含义泄露其他人的统计。原列表pending_count同步收紧为可见范围，字段名保留，记录该权限收紧与回归。

### 2.2 扩展列表与排序

`GET /ai/tool-executions`保留status/risk_level/instance_id/tool_name/cursor/limit；新增：
- period同上，缺省all，兼容原调用。
- sort=`created_desc | expires_asc`，缺省created_desc。待审批UI传status=pending_approval、expires_asc；活动／异常传created_desc。
- expires_asc对非空到期时间升序、空值最后，再以created_at/id稳定破同分；列表仍为AiToolExecutionPage，不改响应形状。
- 带新条件的cursor需绑定workspace、可见用户、筛选和排序，条件不符422；原无新增条件的旧cursor可兼容。时间窗口随请求当前时刻变化，不承诺冻结快照；刷新从第一页，筛选变化取消旧请求。

新增方法`AiToolService.summary(access, *, period, instance_id, risks, tool_name) -> AiExecutionSummaryOut`（async），列表新增period/sort可选参数。新summary不额外执行写入；已有过期清理／worker照旧，统计显式排除到期项。

### 2.3 决策保持

- approve: POST `/ai/tool-executions/{id}/approve`，If-Match=`"version"`；body critical时password，其他{}。reject原body={reason}，不需要password。
- expires_at是创建后15分钟，归零仅禁用／刷新，后端_decidable最终判定；不增加延长或恢复过期请求端点。
- 各状态必须独立：pending_approval/rejected/expired/executing/succeeded/failed/unknown/denied。
- summary是平台说明；arguments是脱敏固定参数；arguments_hash显示摘要即可，不在浏览器重新拼参数发给执行器。
- 新规则说明只能解释现有risk及授权例外。read/knowledge/direct与已授权智能体能力不被强制纳入审批，也不变成全局自动放行。
- source.inferred=true必须标来源推断；无来源／参数缺失不编造理由、前后值、聊天原文。

## 3. 六页管理复用契约

### 3.1 设置与安全

复用PATCH /me、PATCH /workspaces/{id}、成员GET/POST/PATCH/DELETE与auth密码／sessions接口；具体path/body以当前OpenAPI为准，不再造新身份服务。
- 邮箱只读，display_name和workspace.name仍最多64字符，分别保存。
- 添加成员按原existing-user或create-account流程；owner不可变，admin不得授予／撤销admin。
- 密码请求仍current_password/new_password；确认新密码仅前端字段。密码12–128字符；密码更新后其他会话撤销，当前会话保留。
- SessionOut仅id/created_at/last_seen_at/expires_at/current，不新增假设备／IP字段。DELETE按指定session_id，仅本人。

### 3.2 GitHub

- 复用集成设置与现有测试／同步接口，Token格式／最长512／最多20仓库等沿GitHubForm及当前后端验证。
- 已合并逐仓库证据：GET `/developer/repos/{repo_id}/permissions`。Repo投影ID从现有repos列表匹配当前integration/仓库名；不能跨集成或凭Repo.access补四项通过。
- 显式测试的当前IntegrationTestResult.repositories可直接展示；持久快照用上述只读接口。metadata失败导致后续未测时显示未核验，checked_at是上次真实核验时间。
- Token或仓库清单改变后沿既有快照失效；rate_limited使用sync.reason与retry_at，unauthorized不展示过期私有内容。不再新增第二套权限缓存／探测API。

### 3.3 Gmail

- MailSettingsOut的configured/client_id/client_secret_set/redirect_uri/version直接使用，不解析占位符当凭据。
- MailAccountOut的can_read/can_send是实际scope标志；status为active/needs_reconnect/disconnected；agents、agent_signature、last_error与version原样使用。
- 分配PUT `/mail/accounts/{id}/agents`，body={version,instance_ids}，整组替换的是该邮箱授权列表；署名PATCH `/mail/accounts/{id}`仅传其字段与最新version。
- 账户选择、OAuth回调、重新连接／断开沿现有connect/disconnect机制。新邮箱默认agents=[]，分配不打开实例mail.send_enabled。
- 不增Gmail modify scope，不增读正文／附件用于设置页，不把其他邮箱的draft／授权发到当前账号。

### 3.4 新闻早报

- 复用news模块的settings/status/settings-agents等现有GET及owner写接口，路由与key保持模块注册，停用MODULE_DISABLED处理不变。
- NewsSettingsOut.version仅并发保护，config_rev仅生成文件次数，rendered_at/render_error表示文件写入结果，不是任务成功／运行版本。
- deployed/taken_by_other/online=null分别表达；timezone只读，时刻1–3个且半小时步长。
- 两种briefing_mode、filter_method、真实platform_choices、feeds/topics/exclusions、LiteLLM模型配置与channels/learned全部保留。
- 保存、test-push与generate单独动作；dirty时先保存，测试／生成只用已保存配置。渠道密钥按is_set占位，不回填旧原值。admin不能发写请求。

## 4. 密钥库读接口增强

现有owner接口保持，新增：
1. GET `/agents/secrets`：新增q（1–100字符，名称／说明字面包含）、kind（model_api_key/api_token/generic）；在workspace条件下先筛选再分页，LIKE通配符转义。
2. AgentSecretPage新增`total:int`为同筛选的全量匹配数，保留items/next_cursor/total_estimate；limit与原接口一致，cursor带筛选摘要，筛选后不能复用旧cursor。
3. GET `/agents/secrets/{secret_id}`：owner、scope校验，返回AgentSecretOut元数据，绝无value／ciphertext_ref／历史原值。静态/accesses在UUID路由前。

扩展`AgentService.list_secrets(access, *, cursor, limit, q=None, kind=None)`，新`get_secret_metadata(access, secret_id) -> AgentSecretOut`（async）。更新_secrets_out批量转换，避免逐密钥取grant造成N+1。

**已有**GET `/agents/secrets/accesses`支持secret_id/instance_id/result/cursor/limit，直接复用，不再造“按密钥记录”接口。详情默认secret_id=选中项，行数不冒充全历史。result仅ok/not_found/rate_limited，not_found不区分未授权与不存在。

新增／编辑／轮换／删除继续原body/version；value_version与version不同。前端不缓存新值、不让搜索触及密文／值，不生成“查看旧密钥”或导出密钥端点。授权跳转现有智能体授权编辑，避免覆盖其余grants。

## 5. 审计查询

新增`AuditQueryService`（`app/domain/audit_query.py`），复用现有表与脱敏逻辑，不修改只追加写入约束。

### 5.1 过滤与视图

`AuditFilters`参数：
- after/before：可选带时区datetime，成对给出，after含、before不含且after<before。无过滤兼容原全时间倒序；UI缺省最近7天，转UTC提交。
- actor_id:UUID可选；system_only:bool=false，true时只actor_id IS NULL，二者互斥。
- action精确值、action_prefix前缀二者互斥，最长128；resource_type最长64；q最长100，字面匹配action/resource_type/resource_id及已脱敏metadata中的name/title，不搜索密文、ip_hash或原payload。
- 若q匹配内置中文事件标签，可转换到已定义action集合；未知事件仍能按原action查找。字段含义在UI注明，不宣称能搜到不存在的历史对象名称。

扩展GET `/audit-logs`，保留limit=30（1–100）与cursor，加入上述过滤，仍Page响应。筛选均在limit前；不返回未scope的workspace_id=null事件。
新增GET `/audit-logs/{id}`返回`AuditLogView`；新增静态/export路由需在UUID路由之前。

`AuditLogView`继承AuditLogOut，新增可缺省的action_label/actor_label/resource_current_name（str|null）。无通用result字段，不伪造before/after或IP。
- actor_label只在当前空间可见成员资料中解析，actor_id=null显示系统，其余找不到退回ID。
- resource_current_name必须遵守资源当前读取权限：例如admin不可借审计补读owner专属密钥名；未知／无权时null。与记录metadata已有名称分开，补充名标“当前名称”。
- 模型解析仅本地允许类型、批量查，不发第三方HTTP，不因单个对象已删除导致整页失败。
- cursor绑定workspace与过滤，keyset按created_at/id倒序，篡改／条件不符422。无全量计数时显示已加载，不生成假total。

方法：`AuditQueryService.list(access, filters, *, cursor, limit) -> Page[AuditLogView]`、`detail(access, entry_id) -> AuditLogView`，async；新resolver与标签映射放独立文件，现有AuditTrail写入行为保持。

### 5.2 CSV导出

新增GET `/audit-logs/export`，owner/admin、相同filters，强制after/before且最大31天，不接收cursor或limit。UI使用当前筛选，超过范围提示缩小时间。
- 导出读取当前scope、过滤和截止as_of内的记录，最多10000行。先查询至10001，超过返回422 AUDIT_EXPORT_TOO_LARGE，绝不静默截断。用同一有界查询结果生成文件，不导出当前浏览器仅加载的一页。
- 列：id、created_at_utc、action、action_label、actor_id、actor_label、resource_type、resource_id、resource_current_name、metadata_json。不得含ip_hash／原IP／密码／Token／密文引用／原始业务payload。
- metadata_json只取已脱敏事件信息，复用audit.redact并递归去除ip/client_ip/source_ip/ip_hash及凭据类字段；不从业务表补回原始payload。UTC时间ISO格式，UTF-8 BOM、标准CSV引号。所有文本单元格在去除前导空白后若以=、+、-、@或制表／回车开头，前置单引号作为文本；测试带引号／逗号／换行和公式注入。
- Content-Type text/csv、Content-Disposition固定安全文件名、Cache-Control:no-store。只会话内下载，不上传公共对象存储／不生成可复用公开链接。
- 请求校验当前角色／空间，不能凭上次按钮状态或客户端文件缓存绕过；普通成员／其他空间都不能导出。
- 文件生成后在当前事务记录`audit.export_generated`：仅actor、范围、行数及安全过滤摘要；不记文件正文或原始q／metadata。该事件在本次查询截止点之后，不把“文件已生成”说成浏览器下载完成。对此事件写入测试，普通刷新不追加审计。

新增`AuditExportFile(filename:str, content:bytes, row_count:int, as_of:datetime)`与`AuditQueryService.export_csv(access, filters) -> AuditExportFile`（async）；无必需数据库迁移。

## 6. 兼容与验证

- 默认参数、旧Page字段与原写接口保持，新增读字段不使旧UI报错；前端启用新筛选／汇总前需等对应后端契约可用。
- 不在前端当前页做q过滤冒充全量；若接到旧响应缺新total，仅标已加载／功能未提供，不生成新接口已成功的假象。
- 新接口按当前权限测member/viewer/admin/owner及其他空间；敏感输入、CSV、直接深链、已删对象、并发更新、时钟差、限流和失权必须有回归用例。
