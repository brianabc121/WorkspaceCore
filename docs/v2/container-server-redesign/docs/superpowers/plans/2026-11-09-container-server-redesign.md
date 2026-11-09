# Workspace 容器服务器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 人类指定交给Claude开发，当前Codex只交付文档与图片。无上述技能时按等价任务流程执行，不为读方案安装插件。

**Goal:** 将运行服务器页改为容器服务器，以申请智能体为线索展示所有容器环境、真实负载、执行与回收情况。

**Architecture:** 复用RunnerHost／RunnerEnvironment、现有报告和管理员接口；在runner-agent加入CPU差分采样，在服务端加入最新指标时间与兼容DTO，不建时序库。前端把容器总览设为首页，详情复用运行记录和生命周期动作，原模板／设置／权限保留。

**Tech Stack:** Vue 3、TypeScript、Vite、Pinia、TanStack Vue Query、既有UI／CSS变量；FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL、Alembic、现有Python runner-agent与锁定Docker Engine API。前端Node 22与npm ci。

**Spec:** [产品设计规范](../specs/2026-11-09-container-server-design.md)、[指标与接口约定](../specs/2026-11-09-container-server-contract.md)，必须与效果图一起阅读。
**核对基线:** origin/main `e0176b59ffcf2ae3e630d4d1b0f75addcf4b1f6c`，含#177/#178/#179；开工重新fetch最新主干。

## Global Constraints

- 用户可见名称为“容器服务器”；/runner、/api/v1/runner、工具／模型／表命名不整体改名。
- 每工作空间一台容器服务器；owner/admin管理，SSH部署owner；不合并智能体服务器与Runner，不改认证／权限。
- CPU实测核数与配置cpus分开；内存使用memory_stats.usage、限额按MiB；disk_bytes只表示数据卷占用。
- 默认报告60秒、页面读取30秒、指标过期180秒，不承诺图片中的10秒实时，不增加历史曲线或时序库。
- 非running不显示实时CPU／内存；缺测／旧agent／计数器重置为未知，真实零才是0。每项分别标时间和过期。
- 现有环境列表内部截断500条；新增服务端搜索／排序／分页与匹配total，不能只筛当前页。新UI每页50，旧API缺省500保持兼容。
- 保留原容器生命周期、模板、执行记录、快照、权限和SSH设置；保护、归档、删除语义以当前代码为准，不改回收算法。
- 新字段可缺省／为null；先兼容服务端、再升级agent、最后UI。不停止已有环境来完成页面改版。
- API类型由后端导出OpenAPI并机械生成schema.d.ts，禁止手改生成输出；迁移只加新nullable字段。
- 不开放新终端／宿主权限，不引入privileged／Docker socket挂载，不改网络限制，不恢复n8n，不覆盖其他页面改造。

## Review Focus

1. Docker累计CPU重置、容器重建同名、首样本与零增量：unknown和0区别准确，不能串到别的环境（Task 1/2）。
2. 旧agent、单项stats失败、host在线但report中断、旧数值无时间：不盖新时间、不显示假绿色实时（Task 1/2/3）。
3. 停止／归档／删除后卷与快照存在性、pin与删除条件：不混淆数据保留或自动回收规则（Task 2/4）。
4. 同名用途、原智能体已删除／共享、跨空间和迟到请求：详情／权限／归属正确，失权内容清空（Task 2/3/4）。
5. 旧tab深链接、筛选外详情、并行执行、动作结果未知：可定位，可恢复，不重复发送破坏性动作（Task 3/4/5）。

---

## 0. 开工与交付顺序

- [ ] 读最新AGENTS.md/docs/AGENTS.md、.collab任务／交接、D-0003目录归属、D-0004合并规则与当前Sprint批准记录；检查重复任务、在途PR和路径占用。
- [ ] 安全运行`git fetch origin main`与`python3 scripts/collab.py inbox --as claude`，从最新主干在自己的分支工作；保护未提交内容，不reset／clean他人文件，不恢复旧T-0149分支。
- [ ] 读docs/v2/RUNNER_PLAN.md、docs/decisions/2026-11-06-runner-verification.md、runner_agent、RunnerHosts.report、RunnerAdmin、回收规则与OpenAPI；若智能体服务器密码改造在途，避免同时修改共享SSH／导航文件。
- [ ] 按人类指派及协作协议建立／接手实际T编号，声明paths并start。下面Task编号只表示顺序，不预占T编号。
- [ ] 默认五阶段依次形成可审查提交，整包一个主任务PR；也可先合完全兼容的后端提交，但前端不能引用尚未合入的DTO／详情接口。代码、契约、生成类型和任务状态一起交付。

## Task 1：runner-agent真实CPU采样与增量报告

**Files**
- Create: `services/api/app/runner_agent/metrics.py`
- Modify: `services/api/app/runner_agent/agent.py`、必要的`runner_agent/docker.py`
- Modify: `services/api/app/schemas/runner.py`（RunnerReportContainer增量字段）
- Create: `services/api/tests/test_runner_cpu_metrics.py`
- Modify: `services/api/tests/test_runner_agent.py`（FakeDocker与报告回归）
- Generate: `services/api/openapi.json`、`apps/web/src/services/schema.d.ts`
- Create: `docs/decisions/2026-11-09-runner-container-metrics.md`（若已占用按主干选择实际唯一名称）

**Interfaces**
- `CPUSample(used_cores: float | None, window_seconds: float | None)`。
- `ContainerCPUSampler.sample(container_id: str, generation: str, cpu_total_ns: int | None, monotonic_ns: int) -> CPUSample`及`prune(active_container_ids: set[str]) -> None`，细节严格按契约§2。
- RunnerReportContainer新增cpu_used_cores／cpu_window_seconds，两者同时有效或null；默认null，旧报告合法。
- sampler只服务现有report流程；真实Docker ID＋启动代次，当前完整容器entry不因CPU异常丢掉。报告采集不新增高频线程。

- [ ] **Step 1 — RED数值：** 测试10e9/100e9→11.2e9/102e9得到0.6核／2秒；多核增6e9／2秒得到3核；有效零增量得到0。首样本、时间倒退、负值／缺失、计数器回退、代次变化、同名不同ID、窗口<1秒／>180秒都返回契约中的未知与重置行为；prune释放旧ID。
- [ ] **Step 2 — RED报告：** FakeDocker部分stats失败时该容器仍在报告且state不丢，其他容器CPU正常；旧报告不带CPU仍可解析。NaN／Infinity／负CPU／单边窗口字段拒绝；volume Size=-1／缺失为未知，真实0仍为0。超过报告上限不截断后提交伪完整对账。
- [ ] **Step 3 — 确认RED：** services/api中执行`uv run pytest tests/test_runner_cpu_metrics.py tests/test_runner_agent.py -q`，新断言因尚无采样实现失败；环境错误不作为RED。
- [ ] **Step 4 — 实现：** 读取锁定Docker API的累计纳秒，用单调时钟计算；使用可靠StartedAt/启动代次核验重启。采样cache有锁并定期prune；对运行容器复用现有stats，并按需有界读取inspect，不把UI请求变成Docker采集触发器。
- [ ] **Step 5 — GREEN与契约：** 新旧测试通过；在锁定Docker／gVisor验证字段和单位，ADR记录证据。更新生成契约，说明旧服务忽略增量字段／新服务兼容旧agent，升级agent经既有接入包流程，不自动停止工作容器。
- [ ] **Step 6 — 提交：** 采样、兼容schema、测试、ADR和任务日志同阶段提交，不宣称页面已完成。

## Task 2：指标接收、新鲜度与单环境详情

**Depends on:** Task 1。
**Files**
- Modify: `services/api/app/models/runner.py`、`app/schemas/runner.py`
- Modify: `services/api/app/domain/runner_hosts.py`、`runner_admin.py`、`app/api/v1/runner.py`
- Create: `services/api/app/domain/runner_metrics.py`
- Create: `services/api/migrations/versions/<最新主干下一revision>_runner_environment_metrics.py`
- Create: `services/api/tests/test_runner_metrics_api.py`
- Regress: `services/api/tests/test_runner_api.py`、`test_runner_reclaim.py`
- Generate: OpenAPI和前端类型。

**Interfaces**
- 模型加nullable cpu_used_cores、cpu_window_seconds、cpu_reported_at、mem_reported_at、disk_reported_at，沿用旧mem_bytes／disk_bytes，不建历史表。
- `environment_metrics(env: RunnerEnvironment, host: RunnerHost | None, *, now: datetime) -> RunnerEnvMetrics`为纯函数，schema与MetricStatus按契约§5。
- `RunnerAdmin.environment(access: WorkspaceAccess, environment_id: UUID) -> RunnerEnvAdminOut`（async），GET `/api/v1/runner/environments/{environment_id}`，workspace不符404。沿用现有 `_outs`批量映射。
- 原list输出新增metrics／total／next_cursor，q/sort/cursor/limit按契约§6；overview结构保留。扩展`RunnerAdmin.environments(access, *, state, instance_id, lifetime, include_deleted, q: str | None = None, sort: Literal['last_used','cpu','memory'] = 'last_used', cursor: str | None = None, limit: int = 500) -> RunnerEnvAdminList`（async）。所有GET读本地投影，不执行SSH／Docker，不发容器动作。

- [ ] **Step 1 — RED接收：** 新报告保存值与服务端UTC时间，不能写别的host/workspace环境；host普通poll不刷新指标时间，指标更新不改last_used_at／version。CPU／内存失败清未知，其余项继续有效；旧记录不回填“现在”。
- [ ] **Step 2 — RED显示口径：** 0.6核／2核=30%，640MiB／2048MiB=31.25%；>100%数字保留。180秒边界ok、181秒stale；host offline/disabled使合法旧值stale。旧值无时间stale且时间未知，无值unavailable；非running CPU／内存not_running。卷与快照区分，归档后无当前卷占用。
- [ ] **Step 3 — RED权限／回归：** owner/admin可读详情，member/viewer403，跨空间404；用途同名按UUID正确，原智能体删除后环境仍可见且不误挂其他智能体。GET外部调用次数为0。另建501条环境：total正确、每页50、第二页不重漏、最后一条可被服务端q搜到；无效／换空间游标422。CPU／内存排序与DTO新鲜判定一致。原丢失／stray／在途保护、回收保护与快照用例继续有效。
- [ ] **Step 4 — 确认RED：** `uv run pytest tests/test_runner_metrics_api.py -q`，验证缺少新DTO／接口导致失败。
- [ ] **Step 5 — 实现：** 接入原report事务与纯函数；各指标独立时间、正确清空失败项，普通host心跳不冒充容器上报；已有通知等用户可见文本同步“容器服务器”，不改链接协议。DTO标注旧值过期；旧字段兼容，单详情不另写权限体系或N+1查询。
- [ ] **Step 6 — GREEN／迁移：** 新测试和runner_api／runner_reclaim通过；临时库upgrade→旧记录为null断言→downgrade一版→再upgrade。检查无生命周期改变、导出OpenAPI并生成类型；同阶段提交迁移和测试。

## Task 3：名称、容器首屏与智能体用量

**Depends on:** Task 2契约可用。
**Files**
- Modify: `apps/web/src/pages/RunnerPage.vue`、`src/app/navigation.ts`、`src/app/router.ts`、必要的`src/App.vue`宽容器映射
- Modify: `apps/web/src/features/runner/RunnerWorkspace.vue`、`OverviewTab.vue`、`EnvironmentsTab.vue`、`api.ts`
- Create: `apps/web/src/features/runner/ContainerHostStrip.vue`、`ContainerSummaryCards.vue`、`ContainerTable.vue`、`AgentUsageTab.vue`、`containerMetrics.ts`、`container-server.css`
- Modify: `apps/web/src/features/agents/RunnerSwitch.vue`中相关用户可见名称，不改权限字段。
- Create: `apps/web/tests/container-server-pages.test.ts`、`container-metrics.test.ts`
- Regress: `apps/web/tests/runner-pages.test.ts`、`navigation.test.ts`

**Interfaces**
- 保留useRunnerOverview／useRunnerEnvironments、runner/user/workspace查询键及30秒刷新；EnvFilter扩展q/sort/cursor/limit并进入查询键，新UI显式limit=50。筛选／排序／手动刷新回第一页，自动refetch保留有效当前页，选中详情ID可保留；不得直接拉Docker。
- `containerMetrics.ts`统一格式化DTO：CPU实际核数与env.cpus、内存bytes与memory_mb*1024²、数据卷字节；旧metrics=null时CPU未支持、已有mem/disk为时间未知，不补0。
- 表格发出`select(environmentId)`／按现有动作选择事件；详情实现交Task 4。智能体用量复用RunnerInstanceUsage，配额列明确“已分配／上限”，不冒充实测。
- URL tab映射、筛选／详情查询参数按契约§7；/runner与既有API／工具名不变。

- [ ] **Step 1 — RED语义：** 服务端total=501、当页50时卡片仍读overview全量，筛选区显示501并可翻页；某智能体匹配4条时显示匹配4。archived计管理记录不说物理容器。CPU排序参数传服务端，不在当前页重排冒充全量；后端unknown/stale最后。同名用途按ID选择。原智能体删除／共享不能错误显示为当前申请者。
- [ ] **Step 2 — RED格式／兼容：** 0.42/2核=21%、640/2048MiB约31%，stopped显示—，真实运行零增量显示0。stale有时间标签、空时间显示未知；metrics=null旧接口列表仍正常；缺total/next_cursor仅显示已加载数并注明总量未提供，不伪造分页／全量搜索。磁盘列明确数据卷占用，不画伪硬盘配额；无主机不是0台正常。
- [ ] **Step 3 — 确认RED：** `npm run test -- tests/container-server-pages.test.ts tests/container-metrics.test.ts`，保留真实失败。
- [ ] **Step 4 — 实现首屏：** 名称改为容器服务器，主机条／四统计卡／筛选表格按图。复用现有gVisor等支持信息，部分运行时未配置按实际展示，不硬编码全部可用。主按钮管理申请权限链接现有实例设置，无新增手工exec／建容器入口。
- [ ] **Step 5 — 实现其余入口：** overview容器总览、instances智能体用量、templates/runs/settings保留；旧environments链接replace到overview并保留选择。相关通知用户文案更新，历史ADR和代码标识不全局替换。自定义导航保留用户配置，权限守卫继续owner/admin。
- [ ] **Step 6 — GREEN与提交：** 新格式／首屏测试和既有runner／navigation回归通过，含分页时筛选／刷新／迟到页响应；按真实数据校验统计、长用途、IPv6主机名、亮暗和390–1920布局。记录图片与实现的文案调整，不写入示例容器。

## Task 4：容器详情、执行记录与安全生命周期动作

**Depends on:** Task 3。
**Files**
- Create: `apps/web/src/features/runner/ContainerDetailPanel.vue`、`ContainerResourceMeters.vue`、`ContainerLifecyclePanel.vue`
- Modify: `apps/web/src/features/runner/api.ts`、`RunnerWorkspace.vue`、`EnvironmentsTab.vue`、`RunsTab.vue`
- Reuse: `TemplatesTab.vue`、`SettingsTab.vue`、`RunnerSshDeploy.vue`与原管理动作逻辑。
- Create: `apps/web/tests/container-server-detail.test.ts`

**Interfaces**
- `useRunnerEnvironment(scope: string, environmentId: MaybeRefOrGetter<string | undefined>)`消费新GET详情，queryKey为runner/user/workspace/environment/id，带abort signal与角色检查。
- ContainerDetailPanel接收id、打开状态，发出close／viewRuns；支持筛选之外ID定位，当前执行用原useRunnerRuns按environment_id读取，不为主表逐行查输出。
- 生命周期动作复用RunnerEnvAction；最新version、确认文案、WRITE_UNKNOWN查状态，成功后刷新runner相关query及原agents视图，不删掉现有跨页失效。

- [ ] **Step 1 — RED定位：** environment_id直链、筛选外详情、删除404、跨工作空间迟到响应、角色撤销均不残留旧详情；手机关闭后焦点归还原行，键盘可打开／关闭。主机离线仍显示合法过期值，不能把旧CPU当实时。
- [ ] **Step 2 — RED执行／内容：** 同一环境两条运行展示真实列表或已加载提示；queued／timeout／unknown不冒充停止容器。用途／命令／输出含HTML或提示指令按文本，不执行；密钥仍走后端脱敏，不从详情把程序输出写到URL。
- [ ] **Step 3 — RED动作：** start/stop/archive/delete/keep/restore/pin/unpin按现有状态；一次性／会话归档提示数据卷清理，常驻按快照规则。409先刷新，WRITE_UNKNOWN先GET状态，未确认结果不重复发送delete，不提前宣告异步动作完成。打开／刷新详情POST次数为0。
- [ ] **Step 4 — 确认RED：** `npm run test -- tests/container-server-detail.test.ts`，缺少新详情与恢复行为而失败。
- [ ] **Step 5 — 实现：** 资源／执行／配置回收分区，时间／来源与不支持指标清楚；原next_step、delete_after、snapshot字段直接展示，不硬编码15分钟。保护控件仅一处；敏感动作保持原分类确认，关闭总开关／权限撤销时禁用并清缓存。
- [ ] **Step 6 — GREEN与提交：** 新详情及runner旧测试通过，含并行执行、停止后内存消失、卷保留／归档、重试／恢复；验证桌面侧栏与移动抽屉。服务器接入与SSH仍走原设置，不纳入前一份密码改造范围。

## Task 5：真实容器验收、升级与交付文档

**Depends on:** Task 1–4。
**Files**
- Modify: `tests/e2e/runner_browser.cjs`、`tests/e2e/agents_acceptance.py`中原Runner场景
- Modify: `.github/workflows/ci-infra.yml`（确需补新场景／名称时）
- Modify: `docs/v2/RUNNER_PLAN.md`（当前名称说明）、`docs/api-contract.md`、`docs/data-dictionary.md`、`docs/deployment.md`及当前部署指引相关章节
- Create: `docs/verification/<实际任务号>-container-server.md`

- [ ] **Step 1 — 真实指标：** 两个智能体各建容器，分别跑忙任务／空闲任务，经过有效连续样本后CPU有合理差异；同一容器重启／同名重建CPU先未知再恢复；停止后CPU／内存—，数据卷仍按真实量。只验证合理边界／相关性，不用固定0.42核作为真实机器测试期望。
- [ ] **Step 2 — 旧新兼容：** 旧agent不带CPU仍能对账及执行，UI显示未采集；新agent先首样本未知、后有效。模拟report失败但poll仍在线，超过180秒指标stale。采集异常不误报容器丢失，不改现有stop／archive／snapshot流程。
- [ ] **Step 3 — 生命周期／归属：** 两智能体分别筛选，原申请者删除或共享仍归属清楚；保护、停止、归档、快照恢复、撤销删除与既有策略一致。卸载／设置仅在原权限内。普通刷新没有新建／停止／删除副作用。
- [ ] **Step 4 — 浏览器：** 390/768/1280/1440/1920、亮暗、键盘与旧URL。验证模板、运行记录、服务器设置、申请权限入口、详情直链和失权；附真实截图，程序输出不泄漏生产数据。
- [ ] **Step 5 — 记录与完成：** 文档说明CPU是窗口平均、数据卷口径、旧字段兼容、先服务端后agent再UI的升级顺序及可恢复进度。真实Docker无法验证时如实阻塞，不用mock替代并宣称全部验收。

## 6. 验证命令与PR

以下为**Claude实施阶段执行清单**；本包未运行产品测试。

前端apps/web，选择Node 22：

```bash
node --version
npm ci
npm run lint
npm run typecheck
npm run test
npm run check:api
npm run build
```

后端services/api，使用项目测试数据库／Redis：

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests scripts
uv run python -m scripts.export_openapi
uv run python -m scripts.export_openapi --check
uv run pytest
```

生成顺序：后端export→前端`npm run generate:api`→审阅并提交生成差异→`npm run check:api`检查零漂移（该命令用git diff，生成输出先同步到索引／提交）。不手改schema来凑通过。

新增迁移在临时库验证，禁止拿生产环境做降级测试：

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

真实容器场景沿当前ci-infra agents job准备；根目录浏览器命令为：

```bash
node tests/e2e/runner_browser.cjs
```

agents_acceptance.py需要现有CI中的入口／CA／测试服务器参数，使用当前CI等价命令并记录环境；不能不准备服务就跑空脚本。不得在真实用户容器上做归档／删除验收。

根目录：

```bash
python3 scripts/collab.py check
git diff --check
```

- [ ] 按实际T编号记录五阶段验收，代码、测试、迁移、生成类型与自己的任务记录同PR；未完成保留真实日志，不标done。
- [ ] PR标题`[T-主任务实际编号] 容器服务器改版与负载观测`，按模板写最终行为、实际命令／退出码、CPU口径、旧agent兼容、截图和限制。
- [ ] 检查首页／两批四页／智能体服务／Runner工具与模块宿主不回归；不因页面名称变更重命名公共协议。
- [ ] 审查与合并遵守最新D-0004，部署、不可逆迁移、推进Sprint或改协作协议仍按仓库规则，不由本包自动执行。

完成定义：页面按容器与申请者组织，实际指标和配置分离且可核验，未知／过期正确，原管理功能可达，真实CPU／生命周期／兼容和浏览器验收有证据。
