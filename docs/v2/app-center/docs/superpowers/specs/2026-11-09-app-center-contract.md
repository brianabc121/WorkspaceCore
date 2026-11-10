# 应用中心接口与一致性契约

新路径、字段和状态均为本包建议，尚未实现。实施前核对最新OpenAPI；现存接口部分已在4760e34核验。
继续使用X-Workspace-Id、会话认证、CSRF、既有ApiError响应与审计机制。

## 1. 已有接口与兼容

| 接口 | 当前事实 |
|---|---|
| GET /api/v1/modules | workspace成员可读，items完整集合，用于导航/守卫 |
| PATCH /api/v1/modules/{key} | owner+CSRF，body仅enabled；依赖不满足409；停用不删数据 |
| GET /api/v1/navigation | 当前角色可见布局与version，未配置version=0 |
| PUT /api/v1/navigation | owner+CSRF，完整布局替换，带version，冲突409 |
| GET /api/v1/operations/services | 当前workspace服务聚合，包含已启用模块声明的检查，可能执行探测 |
| GET /api/v1/audit-logs | owner/admin，服务端过滤+cursor；无删除/修改接口 |

现有ModuleOut保留key/name/description/version/enabled/default_enabled/ready/missing/depends_on/dependents/tools/widgets/can_manage。
新增state_version:int（缺显式行时0）用于开关的乐观并发；注意version仍是模块发布版本字符串，二者不能混淆。
PATCH body建议扩展为{enabled:boolean,expected_state_version?:int}。新页面必须传版本；旧客户端可省略保持兼容，但也必须走同一依赖锁和权限。
重复提交相同enabled不重新写审计/执行钩子；之前同步失败需显式retry接口，避免重复刷新外部状态。

## 2. 管理聚合接口（建议新增）

静态/modules/management须在/modules/{key}同级动态匹配前注册。

### GET /api/v1/modules/management

owner/admin可读；返回{items:ModuleManagementOut[],summary:{registered,enabled,attention},generated_at}。
不分页（注册表是受控有限集合），不在GET主动探测外部服务。
summary基于全部已注册模块，与前端搜索/筛选无关。
attention对每模块去重：(最新同步failed/unknown) || (enabled && (!ready || 健康缓存为down/degraded))。停用后的外部同步失败同样需要处理，不能因enabled=false而从异常计数消失。
unknown健康/过期检查/unrecorded历史不直接当运行失败；仅停用且无同步异常的模块不算attention。
自定义页面计数取GET/navigation当前用户可见pages.length，与模块总数分开。

### GET /api/v1/modules/{key}/management

owner/admin可读，返回单个ModuleManagementOut；跨workspace/未知模块不泄露内部信息。
ModuleManagementOut包含：
- module: ModuleOut。
- host_api:int、capabilities、health、sync、actions。
- capabilities.tools:[{name,risk}]，来自ToolSpec；widgets:[type]；crons:[{id,kind:"scheduled",schedule_summary}]，不输出Python导入路径；dependencies/被依赖的key与启用状态。
- page入口/role/widget标题由前端对应ModuleDefinition派生，不要求后端解析TypeScript。
- health:{state,checked_at,checks:[ServiceStatusOut],stale:boolean}。
- sync:{state,state_version,attempt_id:UUID|null,target_enabled,reason:null|固定类别,started_at,finished_at}。
- actions:{can_toggle,can_retry_sync,can_check_health}，依据真实角色/状态计算，前端隐藏不代替后端权限。
- 部署前提只输出missing变量名，不输出设置值、内部服务地址、Key、凭据引用或堆栈。
GET列表/详情的缓存读取须可区分“未检测”和“没有探测器”，不能借获取详情悄悄发HTTP请求。

### POST /api/v1/modules/{key}/health-check

owner/admin+CSRF；只检查该模块已声明的ServiceCheckSpec，不接收URL/headers/probe路径。
模块停用返回409 MODULE_DISABLED_FOR_CHECK；无探测器返回not_applicable；缺requires返回not_configured且不请求外网。
复用Operations健康probe和安全HTTP；单服务最多10秒或已有更严格超时，并发最多3个；按module/workspace每分钟最多3次，429给retry_at（如可确定）。
返回ModuleHealthOut，与已有ServiceStatusOut细项兼容。探测失败仍保留checked_at和原因；不是模块停用/数据清空。
健康聚合：无检查→not_applicable；全部not_configured→not_configured；任一down→down；无down但有degraded/部分not_configured→degraded；全部ok→ok；其余unknown。stop/enabled在另一个字段展示。
stale以各检查checked_at和现有缓存TTL为依据，不伪造实时状态；无checked_at时unknown/not_configured不称新鲜探测。

### GET /api/v1/modules/{key}/activity?cursor&limit=30

owner/admin；精确workspace_id + resource_type="module" + resource_id=key，再按时间/ID倒序分页。
复用AuditQueryService或相同安全投影；当前全局audit API没有精确resource_id参数，不能拿q=key冒充精确过滤。
返回items,next_cursor；不编造总数。不返回密钥、路径配置、远端原文。
本期模块活动仅启停、同步及重试，不自动声称包含整个应用业务日志。
历史模块被移除后可从全局审计查询；详情未知模块404，页面给出明确提示。

## 3. 启停与同步持久化

### 数据建议

WorkspaceModule增加state_version整数：现有行迁移为0；无行时0；实际enabled变化递增；不要求回填历史操作。
新增ModuleSyncAttempt：
id,workspace_id,module_key,state_version,target_enabled,state,reason,requested_by,started_at,finished_at,created_at。
复合关联/索引包含workspace；一条状态版本可有多个重试attempt，但同一版本同一时间只能有一个在途执行。
reason固定类别：hook_failed,config_render_failed,timeout,interrupted,stale_revision,busy,result_unverified,not_deployed,application_not_active；只记类别，不存异常正文。
无钩子sync=not_required；有钩子却没有历史记录sync=unrecorded，不回填succeeded。

### 状态机

pending → running → succeeded / failed / unknown / superseded / not_required。
开关事务先提交；hooks失败不回滚enabled。sync的state_version必须对应当前目标。
succeeded必须来自明确的成功结果，不能仅凭钩子未抛异常；它仍不证明服务探测通过或业务推送已开启。
unknown包括有副作用的超时和中断；不能只靠重试次数把unknown写成成功。
旧attempt可以保留真实终态，但不得作为当前目标状态或覆盖新state_version。

### 执行与并发

推荐维持当前“首次开关后立即尝试钩子”的正常体验，加入持久记录、全程有界超时和重试入口；不为此新建通用工作流引擎。
所有旧/新PATCH与重试入口，使用相同的跨进程workspace操作锁；锁覆盖依赖读取、目标写入、首次钩子尝试和结果记录。
可使用专用数据库连接上的session-level advisory lock，并在finally释放；不能用提交即释放的事务锁假称覆盖提交后的hook，也不能用单进程asyncio.Lock代替跨worker锁。
锁忙返回409 MODULE_BUSY且不写目标。持锁总时长有上界，hook建议15秒；锁不阻塞普通读页面/健康查询。
每次执行前重读当前目标；不执行旧state_version钩子。不把用户提交的enabled布尔值当远端唯一事实，模块hook应按最新DB状态协调。
钩子是可信仓库代码，必须可重复协调到相同最终状态；不能承诺超时后的外部操作已撤销。未知结果先查询/协调最新目标，禁止盲目重复部署。
若沿用现有持久任务基础设施替代同步首次尝试，需在同PR明确调整现有测试/客户端兼容与失联恢复，保留本节语义，不半同步半异步无记录。

### POST /api/v1/modules/{key}/reconcile

owner+CSRF，body={expected_state_version:int}；执行同一受控协调器，返回200 ModuleManagementOut（包括成功/失败/unknown），不冒充202后台任务。
不修改enabled，不自动启用依赖，不接受hook路径；重复/并发请求遵守版本与在途锁。
旧版本409 VERSION_CONFLICT，未知模块404，锁忙409 MODULE_BUSY，无hook返回not_required且不造attempt。
崩溃遗留pending/running：超过明确执行期限的读投影显示unknown，owner重试需先获得同一操作锁；拿不到锁不启动第二个执行。
持久同步attempt与审计写入在独立结果事务中，不因原hook失败/rollback而丢失。

审计新增module.sync.succeeded/module.sync.failed/module.sync.unknown/module.sync.retried，resource_id均为模块key，metadata只含state_version/attempt_id/目标/固定reason。
开关module.enabled/disabled保持现有行为；同步结果不重复记录开关事件。审计actor说明用户发起/系统执行，不能伪造操作者。


### 真实新闻钩子的结果与共享资源

新增可选结构化HookOutcome（放modules/base.py）：state=applied|not_required|failed，reason为固定类别，调用参数签名不变。
协调器只把applied转为succeeded；not_required显示未执行/无需同步并保留原因；failed记录失败。旧None返回仍可被调用，但记录unknown/result_unverified，不能回填假成功；无hook声明仍是not_required。
news.hooks.on_toggle必须消费真实返回值：render_current返回非空错误字符串即HookOutcome.failed(config_render_failed)，先保留错误记录事务，不向页面输出原错误正文。render_current现有str返回契约可以保持，不能破坏其他调用者。
未部署或没有启用新闻业务设置、没有实际写入时返回not_required，不能显示外部配置已成功同步。

TrendRadar配置文件是部署级共享资源。仅锁模块PATCH/reconcile不足：保存新闻设置、relearn_and_render、反馈新增排除词、worker/部署命令等也可能调用render_current写同一文件。
所有实际文件渲染入口必须共用跨进程news资源写锁，资源键按实际配置目录/部署实例固定，不按workspace分割，也不接受用户输入任意锁键。
锁覆盖读取当前模块开关/新闻设置、渲染输入、文件写入和对应结果记录。排队写入取得锁后重新读取最新状态，不写排队前已构建的旧配置。
修改配置输入的入口需在获取业务行锁或修改输入之前拿资源锁，避免先锁NewsSettings行、再等资源锁造成死锁；保留用户事务的原子性，不能refresh覆盖尚未提交的用户修改。
worker使用新事务/明确刷新读取最新状态。两类锁统一顺序workspace操作锁→news资源锁→业务行锁；只渲染入口可只拿资源锁，不能反向取得workspace锁。
资源写锁在finally释放，不使用单进程锁。停用成功后的迟到设置/worker/反馈渲染不得重新打开采集/推送。
若外部写入超时，标unknown并协调最新目标，不声称副作用已撤销。扫描所有cfg.write_files或其他写同目录的入口，一并接入规则，不能只改on_toggle包装器。

## 4. 前端管理元数据与导航

ModuleDefinition.management可选：
{primaryPath?:string,settingsPath?:string,category?:string,helpPath?:string}。
primary/settings必须属于相同模块已注册route；helpPath只能是受控站内帮助路由或静态安全链接。
新闻模块声明primaryPath=/news、settingsPath=/settings/news。
AppCenter viewModel合并backend module.key与frontend registry，未匹配时显示“前后端版本不一致”，不猜路径。
声明页面数来自该模块唯一nav路径数；组件数来自声明widgets；工具与cron来自后端，不拿实际实例可用量冒充。
无设置路径隐藏设置按钮；按nav/route原角色限制渲染，不覆盖角色/审批。

添加/编辑入口：复用GET/PUT/navigation及PageForm；不新增另一套Page表或“每页独立保存”的无版本假接口。
整份替换必须带读到的version；冲突时保留草稿，读最新完整布局后显式合并并再次保存，禁止自动覆盖。
未编辑的隐藏owner页面不应被admin保存移除：admin根本无PUT权限；owner保存时取完整owner快照，不复用更低角色缓存。
删除入口不删目标对象；embed遵守EMBED_ORIGINS和sandbox，编辑可见角色不等于赋予资源权限。

## 5. 统一错误与安全边界

新增可读错误：MODULE_BUSY、VERSION_CONFLICT、MODULE_DISABLED_FOR_CHECK；依赖继续MODULE_DEPENDENCY。
健康/同步错误不写成空模块列表；保留旧列表时展示stale与更新时间。
每个新API检查workspace成员资格、owner/admin级别；前端can_manage只作展示。
模块诊断不输出Settings值、凭据/密文、导入路径、内部地址或远端原始响应。
跨用户/workspace清理store与请求；迟到回包不能覆盖当前身份。
本期不开放清除数据、卸载模块、任意代码导入、外部应用授权或新增AI免审批权限。
