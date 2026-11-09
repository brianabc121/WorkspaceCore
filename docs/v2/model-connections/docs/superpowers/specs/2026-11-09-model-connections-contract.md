# 中转站管理接口与一致性契约（建议新增，尚未实现）

本文件中的新接口、表和类型均为本次建议契约。实施时先核对最新主干，并在首个后端 PR 导出真实 OpenAPI；不得把建议误报成现存能力。
已有 /api/v1/agents/secrets* 与 /agent-api/v1/secrets/resolve 继续兼容。错误包结构、CSRF、workspace header、Idempotency-Key 遵守现有项目惯例。

## 1. 资源模型

### ModelConnectionOut

| 字段 | 类型/含义 |
|---|---|
| id, workspace_id | UUID；不接收客户端指定 workspace_id 覆盖 scope |
| name, description | 名称 1..64 字符、workspace 内唯一；说明最多 500 |
| connection_type | official / relay / null（迁移草稿） |
| official_provider | openai / anthropic / google / null；仅核验过的预设可选 |
| protocol | openai_compatible / anthropic_native / gemini_native / null |
| base_url | HTTPS base URL 或 null；不含 userinfo/query/fragment，最多 2048 |
| credential_configured | boolean；绝不输出 value、credential_ref、secret_id 或密文 |
| credential_version | int；沿用内部 secret.value_version，轮换时递增 |
| model_scope | selected / all_available |
| configuration_state | draft / ready / needs_review |
| enabled | boolean；与健康状态独立 |
| migration_state | native / needs_configuration / needs_classification / managed / non_model |
| legacy_source_id | UUID 或 null，owner 可见，用于迁移追溯，不可作为取 Key 接口 |
| model_count, grant_count, binding_count | 完整统计，不是当前页长度 |
| config_revision | int；URL/协议/模型配置/Key 更换时递增 |
| version | int；乐观并发版本，任意写入递增 |
| last_check | 检测结果/检查时间/关联 config_revision；或 null |
| created_at, updated_at | UTC ISO8601，前端按用户时区展示 |

required ready 条件：类型、经支持协议、URL、可解密 Key、非空明确模型集合均具备。
ready 不等于服务商已接受推理请求。protocol 与 connection_type 相互独立：第三方也可能使用原生协议。

### 模型记录

ModelConnectionModelOut = {id: UUID, model_id: string, display_name: string, source: manual|discovered, availability: listed|verified, last_seen_at: datetime|null, version: int}。
model_id 原样保留大小写和服务商 ID；不得把“GPT/Claude/Gemini”标签或通配符当真实 ID。
每条连接 model_id 唯一；1..128 字符，不允许控制字符/空白；每条连接最多 1000 个明确模型。
selected 必须有至少一个手工/勾选 ID；all_available 必须有一次完整同步快照。同步暂不支持时只能切到 selected 手工填写。
服务商列出的 ID 不一定可调用；只有实际模型探测成功才记 verified，不误导为所有列表模型都可推理。

### 授权与绑定

ModelConnectionGrant = {connection_id, instance_id, created_at}；复合外键包含 workspace_id。
AgentModelBindingOut = {instance_id, connection_id: UUID|null, model_id: string|null, desired_revision, applied_connection_id: UUID|null, applied_model_id: string|null, applied_revision: int|null, revoked: boolean, apply_state, last_error_code: string|null, job_id: UUID|null, version}。
每实例一个主绑定；一个连接可以多授权、多绑定。应用快照必须保存applied_connection_id/applied_model_id，不能只记一个版本数字。切换期间旧已应用连接与新目标连接均可作为受控取用候选，前提是连接仍启用、原授权仍有效、binding未撤销；确认新目标后退休旧模型候选。旧alias若仍被Runner引用，不可顺手清理。
apply_state = legacy_active | pending | applying | applied | failed | unknown | manual_required。
旧手工配置未迁移的实例为 legacy_active，不为其猜测 connection_id 或 model_id；模型选择器与绑定 Out 分开处理该兼容状态。

### 检测结果

ConnectionCheckOut = {id, connection_id, config_revision, mode, model_id: string|null, status, reason: string|null, checked_at, latency_ms: int|null, retry_at: datetime|null}。
mode = metadata | inference。
status = passed | failed | rate_limited | unsupported | unknown。
reason 只能是固定类别：unauthorized, forbidden, timeout, unreachable, tls_error, invalid_response, model_not_available, protocol_unsupported, models_endpoint_unsupported, destination_not_allowed, credential_unreadable, stale_revision。
不保存远端原始 body/headers。metadata 通过只证明认证/元数据接口可用，不能显示成模型推理通过。
metadata 结果 15 分钟后在 UI 标记旧结果；不自动变成失败，也不据此停用连接。

## 2. Owner 管理接口

以下统一位于 /api/v1/model-connections：

| 方法/路径 | 输入 | 输出及约束 |
|---|---|---|
| GET / | q, type, configuration_state, enabled, check_status, cursor, limit(默认50/最大100) | items,next_cursor,total；搜索参数影响列表和 total |
| GET /capabilities | 无 | enabled,supported_protocols,official_presets,limits；仅owner，未核验协议不出现 |\n| GET /summary | 无 | total,check_passed,needs_attention,unique_granted_instances；不受分页或筛选影响；check_passed只计ready+enabled且匹配当前revision的15分钟内成功检测，按mode注明 |
| GET /migration/entries | state,cursor,limit | 导入项/待归类/非模型项分页，不含Key；静态路由在/{id}之前注册 |\n| POST /migration/entries/{entry_id}/classify | version,purpose(model或non_model) | 原连接Out及分类状态；保留映射/secret/旧授权，规则见下文 |\n| GET /{id} | 无 | ModelConnectionOut；404 不泄露跨 workspace 存在性 |
| POST / | name,description,connection_type,official_provider,protocol,base_url,api_key,model_scope,models,save_as_draft | 201；创建元数据+加密凭据事务；支持 Idempotency-Key |
| PATCH /{id} | version + 允许编辑字段（不含 api_key） | 200；省略=不变，null 仅可用于草稿可空字段 |
| POST /{id}/rotate-key | version,api_key | 200；版本递增、旧值不再下发、检测失效；不会替用户去服务商创建/撤销 Key |
| DELETE /{id}?version= | 当前 version | 204；有任何有效授权/绑定/在途应用任务则409，禁止级联“顺便删智能体” |
| GET /{id}/models | cursor,limit | items,next_cursor,total,synced_at,sync_state；独立分页 |
| POST /{id}/models/sync | version | 200 models 同步结果摘要；最多一次完整分页同步，失败保留上一完整快照 |
| PUT /{id}/models | version,model_scope,models | 200；整体替换明确模型集合；删除被绑定 ID 则409列影响实例 |
| POST /{id}/checks | version,mode,model_id? | 200 ConnectionCheckOut；inference 必须明确选模型 |
| GET /{id}/grants | cursor,limit | 授权实例分页与总数 |
| PUT /{id}/grants | version,instance_ids | 200；整体替换，拒绝跨 workspace；删除在用实例则409先切换/解除主绑定 |
| GET /{id}/activity | cursor,limit,instance_id?,result? | 创建/配置/轮换/授权/取用/应用事件的脱敏分页投影 |

连接 POST 的 api_key 使用 SecretStr，长度 1..8192；models输入为[{model_id,display_name?}]，不接收客户端覆盖source/verified。新建非草稿须ready，默认enabled=true；save_as_draft=true默认enabled=false。迁移草稿按迁移手册继承旧访问状态，与新建草稿不同。已存 Key 的输入框空白代表“不改”，不能提交空串意外清空。
新建草稿允许 URL/协议/模型未填，仍须明确需要保存的 Key；legacy 解密失败的记录 credential_configured=false。
禁用通过 PATCH enabled=false：有影响实例时先返回影响摘要供 UI 确认，提交显式 confirm_impact=true + version 后生效。
导入草稿可保留 enabled=true 来维持旧授权；ready/当前绑定仍是原生路径必需条件。用户显式停用阻止新绑定、后续配置/凭据取用（含旧alias与Runner）；已取到进程内的 Key 不保证立即停止工作，不自动执行破坏性停容器动作。
DELETE 清理策略：受管secret有legacy/Runner引用时DELETE返回409，提示先处理引用或显式归类为non_model进入兼容凭据；归类保留同一治理映射与停用/撤权状态，不能“删除映射”后绕回旧权限。彻底删除要求所有引用清零。
PATCH：已完成连接配置且有授权或原生绑定时，URL/protocol变化返回409 CONNECTION_ENDPOINT_IN_USE，引导复制为新连接；同一主机的路径变化也按目标变更处理。例外：旧导入项首次从null补齐URL/协议、且没有原生已应用绑定时允许保存，即使保留旧Grant；只保存元数据，不修改旧运行时，不自动切换。后续非空目标变化恢复在用限制。
Key 轮换与 URL/protocol 修改为独立操作，不允许用一次请求同时更换目标及 Key。
前后端不得直接调用任意第三方 API 测试浏览器里的 Key；所有检测通过后端受控 HTTP 客户端。


### 迁移分类与兼容凭据状态

导入记录的entry_id是迁移映射ID，不是用户输入任意secret_id的通道。
classify purpose=model：保留同一ModelConnection/secret/旧授权；缺URL/协议/模型仍needs_configuration，不自动创建新绑定。
classify purpose=non_model：保留ModelConnection映射作治理与追溯，migration_state=non_model，从主模型连接列表/选择器排除，在“迁移与兼容凭据”可见。
non_model项的编辑/轮换/删除可沿用已有agents/secrets接口，但必须经同一个托管凭据服务：owner/workspace/version/显式停用规则统一，不能出现两套互不知情的Key写入。
明确保留AgentSecretGrant供Runner/旧alias使用；ModelConnectionGrant仅作迁移授权追溯，不赋予原生模型能力。
如已有原生目标/已应用绑定或在途任务，改判non_model返回409，先完成切换/解绑。重新归为model仍使用原映射和原secret，不新建重复连接；原授权不扩大、enabled不自动改变，完整配置与新绑定仍需校验。
迁移分类修改connection.version，旧secret接口对托管项写入时同步版本，保证UI冲突可察觉。\n权限不变量：分类/转兼容不得恢复访问。显式撤销连接对某实例授权时，在同一事务撤销对应ModelConnectionGrant及该secret对实例的旧AgentSecretGrant（包含Runner引用的后续取用权限）；分类不重建它们。non_model仍保留治理映射，enabled=false继续阻止旧resolver/Runner。恢复访问必须由owner显式重新启用/授权，不能由分类或迁移脚本顺带执行。
GET列表total与summary.total排除non_model；needs_classification可以在全部连接中标“待归类”，兼容页单列non_model统计。

## 3. 绑定与应用接口

Owner：
- GET /api/v1/agents/instances/{instance_id}/model-binding → Binding 或 null，并给 legacy_active 元信息。
- PUT 同路径，输入 {version,connection_id,model_id} → Binding（首次 version=null，已有时必传）。
- DELETE 同路径?version= → 200 BindingOut，目标connection_id/model_id置null、revoked=true，保留已应用快照用于诊断、版本递增；拒绝后续模型Key取用并安排移除配置。现有进程可能缓存Key，显示待应用，不删除连接。
- POST /api/v1/agents/instances/{instance_id}/model-binding/apply，输入 {version} → 202 ModelApplyJob。
- GET /api/v1/agents/instances/{instance_id}/model-binding/jobs/{job_id} → {id,state,desired_revision,attempt,last_error_code,created_at,finished_at}。
ModelApplyJob.state = queued|running|succeeded|failed|unknown|manual_required；无远端原始输出、明文或完整配置转储。

PUT 绑定必须验证连接 enabled、ready、授权包含该实例、模型在明确集合内、协议适配已核验。
新部署请求由owner明确选择connection_id/model_id，选择即明确授权给即将创建的新实例。在同一事务内新建instance、对应连接Grant和Binding，然后排部署任务；失败一起回滚，Idempotency-Key重放不得重复授权。创建态选择器列owner可管理的ready+enabled连接，不按尚不存在的实例Grant过滤；已有实例的选择器按其授权筛选或先显式授权。不允许先启动默认openai再悄悄切换。
工作台不允许智能体自行改自己的主连接或增加授权。

实例认证接口（沿用现有内网/外网入口与认证）：
- GET /agent-api/v1/model-config → 自己的非敏感配置 {binding_version,desired_revision,provider_key,protocol,base_url,model_id,secret_ref_id} 或409 MODEL_CONFIGURATION_INCOMPLETE。
- POST /agent-api/v1/model-config/applied → {binding_version,desired_revision,result,reason?}；只能回报自己当前的目标版本，迟到确认不能覆盖新版本。
- POST /agent-api/v1/model-credentials/resolve → {connection_id,secret_ref_id}，只给当前目标或尚未退休的已应用连接且已授权的值，返回 {values:{secret_ref_id: value},errors:{secret_ref_id:{code:"NOT_FOUND"}}}（成功/失败按现有resolver语义分项），由受控resolver适配OpenClaw协议。
敏感响应 Cache-Control:no-store；不写日志。解析器的业务标识来自连接，不能由客户端把任意 secret_id 当 connection_id 使用。
外网网关必须明确加入上述新路由白名单并测试内/外网；不得直接开放全部 /agent-api。

配置应用不自动在 HTTP 请求里跑 SSH。复用已有受控任务/连接机制或持久任务；同实例串行，幂等键为 instance_id + binding_version + desired_revision。
任意时刻只应用最新目标；对同服务器不同实例锁分别隔离，不能锁全服务器导致不必要阻塞。
解除绑定的应用必须有清晰“停止使用模型”的运行时行为。若锁定上游无法安全移除运行中模型，返回 manual_required，不能声称已撤销已缓存 Key。

## 4. 模型同步与检测约束

内部协议枚举不是 OpenClaw 的原生枚举，MC-0 必须逐一记录映射、URL拼接、鉴权、模型列表/推理的真实契约。
最低交付必须有 OpenAI 官方与经验证的 OpenAI 兼容中转，多模型能力完整。Anthropic/Gemini 原生选项只有完成核验和真实 OpenClaw 测试后开放；若需求中任何目标协议未能完成，作为剩余阻塞记录，不标整体协议覆盖完成。
base URL 是协议基地址，不是已经带 /chat/completions、/responses、/messages 的单次请求地址。后端规范化尾斜杠，不错误拼出 /v1/v1。
模型同步最多 1000 条、合计响应最多 2 MiB，发现被截断则标 partial 并保留旧完整快照，不能把已绑定模型默默移除。
模型列表不支持时允许 selected 手工配置与单模型推理验证；all_available 不可伪装可用。
推理检测 UI 先说明“可能产生少量服务商费用”；固定最短提示和最小合理输出上限，不使用用户聊天记录，不自动重试生成。
timeout 建议连接5秒/总读15秒；GET 元数据最多一次额外重试，仅限现有客户端判定安全的读错误；429不立即重试。
429 解析 Retry-After 秒数或 HTTP 日期，提供合法 retry_at；无合法值时返回 null + 明确稍后重试，不捏造时间。
结果仅在 config_revision 仍匹配时更新 last_check；旧异步结果不覆盖新 Key/URL 的检查状态。
模型同步、检查采用每 workspace/connection 的限流，建议同连接1个在途请求、每 owner 每连接每分钟最多5次。

## 5. 网络、日志与错误

复用 app/integrations/http.py 的 OutboundPolicy、检查后固定 IP、原 Host/SNI、关闭跨站跳转与有界超时；允许自定义 base URL 不等于可以扫描平台内网。
默认 HTTPS。私有中转部署通过明确部署 allowlist + TLS 配置，不在表单提供“忽略证书验证”开关；开发测试域名和明文 HTTP 只在测试配置允许。
阻止 loopback、链路本地、metadata IP、混合公私 DNS；不要只验证保存时，检查每次外连。
连接 Key 只可发到该记录规范化的目标；拒绝 URL 内 query、userinfo、fragment 和控制字符；不接受用户定义 Authorization header。
禁止在日志、审计、异常、OpenAPI示例、下载包、浏览器缓存里出现 Key、credential_ref、远端原始错误正文。

建议业务错误：
MODEL_CONNECTION_NOT_READY、MODEL_CONNECTION_DISABLED、MODEL_NOT_ALLOWED、CONNECTION_IN_USE、
CONNECTION_ENDPOINT_IN_USE、MODEL_BINDING_CONFLICT、MODEL_CONFIGURATION_INCOMPLETE、MODEL_PROTOCOL_UNSUPPORTED、
VERSION_CONFLICT、CREDENTIAL_UNREADABLE。
不同资源缺失/越权不通过错误消息泄露存在性；遵守现有401/403/404分工。
