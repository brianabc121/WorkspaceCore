# Workspace 知识库／早报／开发者／运维 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 人类指定把本包交给 Claude 开发。本次 Codex 仅交付文档与图片，不启动实现。Claude 无上述技能时按等价流程逐任务实施，不为读取方案安装插件。

**Goal:** 按四张效果图改造知识库、早报、开发者、运维，让信息层级清楚、统计可核验、失败可解释，并保留既有操作能力。

**Architecture:** 保留现有 Vue 页面与领域服务。知识库补一个读侧统计接口，开发者补本地投影聚合与逐仓库权限证据；早报和运维复用现有契约。四页分别交付，新闻继续通过已有模块注册，不做全站重构。

**Tech Stack:** Vue 3、TypeScript、Vite、Pinia、TanStack Vue Query、Tailwind/shadcn-vue/reka-ui、Lucide、d3-force；FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL。前端使用 Node 22（满足 >=22.12.0）和 npm ci。

**Spec:** [设计与产品规范](../specs/2026-11-09-knowledge-news-dev-ops-design.md)，必须与本计划及附图一起阅读。
**核对基线:** origin/main `5f5e9c88b652ad938cef3941be6957642f15bb47`，已包含首页 PR #177；开工时重新 fetch。文件若在新主干迁移，沿用新位置，不恢复旧实现。

## Global Constraints

- 范围仅第二批四页；不重复上一包任务／项目／日历／邮件，不重做已合并首页，不恢复 n8n。
- 沿用现有角色、workspace scope、模块宿主、SSE 失效、路由、乐观并发和幂等；不扩大权限。
- 新闻仅改 `apps/web/src/modules/news` 对应模块；不迁回旧 `features/news` 或 `domain/news.py`，不强制抽取其他核心模块。
- 图片数字与记录全部是示例；真实权限、接口及 Spec 优先于图中按钮和文案。
- 不引入新图谱／图表／Markdown 框架；复用现有组件、CSS 变量、安全渲染和 d3-force。
- 所有未知统计显示未知；0 仅用于确定的真实零。不能用第一页、图谱截断节点或缓存长度冒充总量。
- 新第一方接口先在后端建立契约，再机械生成 `services/api/openapi.json` 与 `apps/web/src/services/schema.d.ts`，禁止手改生成文件。
- 知识总览不新增笔记版本或智能体阅读计费；开发者 GET 不访问 GitHub；早报刷新不生成／推送；运维普通刷新不执行存储写入自检。
- 查询键保留用户与工作空间；失权立即移除相应内容。局部错误不抹除其他有效区域，迟到响应不能串空间或覆盖新筛选。
- 本期无需数据库迁移；权限快照放既有配置 JSON。确需改表时补 ADR、加法迁移与测试，不改历史迁移，不自动执行不可逆操作。
- 不清理／重置他人的未提交工作；不改其他进行中任务的占用路径，不因本计划改变协作协议或推进 Sprint。

## Review Focus

1. 知识库超过2000图谱上限或100条分页、同名笔记跨空间、隐藏／删除端点：统计仍真实，按ID取详情（Task 1/2）。
2. 无三段结构或来源链接的 AI 正文、receiving片段、TrendRadar故障及模块停用：不编造来源或重复生成（Task 3）。
3. GitHub限流、Token撤销、只完成一项的权限检查：保留合法旧数据和时间，失权清理，未测项不标通过（Task 4/5）。
4. 多仓库同分支／不同commit、旧投影与失败重跑：不误贴PR的CI结果，不把同步时间说成操作事件（Task 4/5）。
5. 指标缺测、多服务P95、Grafana单独故障与无存储副本：不补零、不平均分位数、不误报；页面刷新无探测写入（Task 6）。

---

## 0. 开工、拆分与依赖

- [ ] 读最新 `AGENTS.md`／`docs/AGENTS.md`、`.collab/tasks`／handoffs、D-0003目录归属、D-0004合并规则及相关 Sprint 批准记录。
- [ ] 检查工作区、分支、PR、任务与进度日志是否已有相同执行；有正在进行或路径重叠的交付则接续／等待，不重复实现。
- [ ] 安全执行 `git fetch origin main` 和 `python3 scripts/collab.py inbox --as claude`。从最新主干在自己的分支开工；保护现存未提交内容，不从旧 T-0149 残留分支恢复。
- [ ] 把本包 `docs` 和 `assets` 按相对位置保留在交接目录；实施前记录本次人类指派。按仓库协议建立／接手实际任务号，再 `start <实际任务号> --as claude`；不预占本计划里的 Task 编号为 T 编号。
- [ ] 阅读 `docs/v2/KNOWLEDGE_PLAN.md`、`docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md`、`2026-10-30-github-rest-and-affine-verification.md`、`2026-10-31-sprint-5-operations-automations-contract.md`、`2026-11-08-feature-modules.md` 及 `services/api/openapi.json`，采用最新已接受契约。
- [ ] 默认顺序：Task 1→2（知识库）、Task 3（早报）、Task 4→5（开发者）、Task 6（运维）。每项可独立审查；前端依赖的后端契约先合并，不交付引用不存在接口的页面。
- [ ] 共享文件串行改动，尤其路由、工作台壳、OpenAPI、生成类型和主题。已有首页／上一包共享原语可复用；缺少时放本页局部组件，避免新建一套全站设计系统。

下文接口均为**拟议第一方契约**，不是声称现有后端已支持。第三方接口一律以已核验 ADR 为准。

## Task 1：知识库总览的真实聚合接口

**Files**
- Modify: `services/api/app/api/v1/knowledge_base.py`、`app/schemas/knowledge_base.py`、`app/domain/knowledge_views.py`
- Reuse: `app/domain/knowledge_base.py` 的知识权限／空间规则、现有笔记与关联模型；不另写权限体系。
- Create: `services/api/tests/test_knowledge_overview.py`
- Regress: `services/api/tests/test_knowledge_base_api.py`、`test_knowledge_units.py`、`test_knowledge_tools.py`
- Generate: `services/api/openapi.json`、`apps/web/src/services/schema.d.ts`

**Interfaces**
- 新增 `KnowledgeOverviewSummary`：`note_count:int`、`space_count:int`、`link_count:int`、`updated_note_count:int`，均非负。
- 新增 `KnowledgeOverviewOut`：`summary:KnowledgeOverviewSummary`、`recent_notes:list[NoteSummary]`、`generated_at:datetime`，不带正文。
- 新增 `KnowledgeService.overview(access: WorkspaceAccess, *, space_id: UUID | None, updated_after: datetime, updated_before: datetime, recent_limit: int = 6) -> KnowledgeOverviewOut`，位于 `knowledge_views.py` 的现有 `KnowledgeService(KnowledgeBase)`。
- 路由 `GET /api/v1/knowledge/overview`：`X-Workspace-Id`、viewer可读；`space_id`可选；`updated_after`与`updated_before`均必填、必须带时区且前者小于后者；`recent_limit`默认6，范围1–20。参数不合法422，跨工作空间或不可见空间404。
- 总笔记／空间为当前可见、未删除范围的全量；选中空间时空间数为1，包括无笔记空间。归档沿用原可见性，不自动排除。
- `updated_note_count`只计算所选范围内 `updated_at ∈ [updated_after, updated_before)` 的不同笔记；这两个时间参数不截掉总数与最近笔记。
- `link_count`对两端均可见且未删除的笔记关系按有向 `(source,target)` 去重；选空间时至少一端在该空间。资源／文件边不计。`recent_notes`按 `updated_at DESC, id DESC`，同一笔记只出现一次。

- [ ] **Step 1 — RED用例：** 在新测试中建立2105篇可见笔记（跨多个分页）、一个不可见工作空间、一个空空间、一个可见归档空间、重复边及删除端点。断言总数2105不受2000图谱上限影响，空空间返回1个空间／0篇，重复边只计一次，隐藏／删除端点不计。再验证同一笔记多次修订只贡献一次本周更新、窗口上下界及不带时区422。
- [ ] **Step 2 — 确认RED：** 在 `services/api` 执行 `uv run pytest tests/test_knowledge_overview.py -q`；应因新接口／schema尚未实现失败，而非连接数据库失败。保留真实输出。
- [ ] **Step 3 — 实现：** 在现有查询可见性基础上批量聚合；不要逐笔记查询或取全正文后在Python计数。路由调用上述方法，生成时间使用服务端UTC。不让普通阅读产生版本、审计写笔记或智能体读用量；空间初始创建维持原机制。
- [ ] **Step 4 — GREEN与契约：** 跑新测试及上述现有回归，检查不同角色／空间隔离；导出OpenAPI并生成前端类型，确认参数的required、格式、范围和返回nullable声明一致。不新增数据库迁移。
- [ ] **Step 5 — 提交：** 后端、契约、生成类型与自己的任务记录同一提交／PR；使用实际T编号，例如 `feat(knowledge): 增加知识总览统计（T-实际编号）`。

## Task 2：知识库沉浸式总览与关联阅读

**Depends on:** Task 1已提供并合并契约。
**Files**
- Modify: `apps/web/src/pages/KnowledgePage.vue`、`src/router.ts`
- Modify: `apps/web/src/features/kb/api.ts`、`kb.css`、`KbGraphView.vue`（仅需复用／修复清理逻辑时）
- Create: `apps/web/src/features/kb/KbOverviewView.vue`、`KbOverviewStats.vue`、`KbOverviewPreview.vue`
- Reuse: `KbForceGraph.vue`、`KbNoteView.vue`、现有笔记编辑与安全Markdown能力
- Create: `apps/web/tests/kb-overview.test.ts`
- Regress: `apps/web/tests/kb-pages.test.ts`、`knowledge-pages.test.ts`、`browser-knowledge-calendar.cjs`

**Interfaces**
- `api.ts`新增 `useKnowledgeOverview(scope: string, filters: MaybeRefOrGetter<KnowledgeOverviewFilters>)`；`KnowledgeOverviewFilters={space_id?: string; updated_after: string; updated_before: string; recent_limit?: number}`，数据使用生成 `KnowledgeOverviewOut`。
- 查询键沿用 `kbKey`，包含用户／工作空间／空间与时间窗；选中笔记正文及depth=1局部图谱沿用现有 `useNote`／`useLocalGraph`，分别查询，不阻塞总览。
- `KbOverviewStats`只接收 summary、generated_at、加载／错误状态，不计算全局数字；`KbOverviewPreview`接收当前笔记ID与空间上下文，发出选择／打开事件，不承担写操作。
- `/knowledge`落到总览，`/knowledge/graph`保留完整图谱；旧 `/knowledge?focus=<id>`转换到完整图谱并保留其他有效查询参数。桌面功能条增加总览入口、图谱入口改为显式路径；手机原有四入口语义保持。

- [ ] **Step 1 — RED用例：** 写 `shows_server_totals_not_graph_size`（summary2105而预览只有3节点，仍显示2105）；`same_title_uses_id_and_clears_lost_note`（同名不同ID不串，404/失权清空）；`legacy_focus_keeps_graph_link`；`late_old_workspace_response_is_ignored`；`preview_disposes_simulation_and_has_list_fallback`。验证viewer能读不能编辑、归档人类编辑遵从现有权限。
- [ ] **Step 2 — 确认RED：** `npm run test -- tests/kb-overview.test.ts`，因新总览／路由未落地而失败。
- [ ] **Step 3 — 接口与状态：** 用界面显示时区计算周一00:00至下周一00:00并转UTC传必填时间窗；显示时区沿用项目既有选择。总览默认最近更新的可读笔记，空空间保持真正空态；数据失败显示未知／局部重试，不拿旧选中详情顶替新空间。
- [ ] **Step 4 — 界面与交互：** 按图实现空间导航、四项真实统计、局部关联、笔记阅读卡和最近更新；图例复用TYPE_GROUPS与当前可见集合。完整图谱着色／过滤／截断提示、编辑／版本／冲突／回收站／README／AFFiNE入口均可达。保持图谱simulation、resize与键盘监听的卸载清理。
- [ ] **Step 5 — GREEN与视觉：** 新测试及既有知识库回归通过；用无数据／重名／无关联／2000+笔记／超长标题验证。检查390/768/1280/1440/1920、亮暗主题、键盘与图谱列表替代；安全Markdown不执行脚本或远程图片。
- [ ] **Step 6 — 提交：** 仅知识库前端、相应测试和任务记录提交；不覆盖已合并首页。附总览、完整图谱、移动详情截图。

## Task 3：早报阅读页改造，不新增生成流程

**Files**
- Modify: `apps/web/src/modules/news/NewsWorkspace.vue`、`NewsTodayTab.vue`、`NewsBriefingView.vue`、`NewsArchiveTab.vue`、`NewsItems.vue`、`NewsItemRow.vue`、`news.css`
- Modify if needed: `apps/web/src/modules/news/format.ts`、`api.ts`；复用已有请求，不增加写端点。
- Create: `apps/web/src/modules/news/NewsReadingSidebar.vue`
- Create: `apps/web/tests/news-reading-redesign.test.ts`
- Regress: `apps/web/tests/news-pages.test.ts`；模块行为对应 `services/api/tests/test_modules.py`、`test_news_api.py`、`test_news_config.py`

**Interfaces**
- 读取现有 `NewsTodayOut`／`NewsBriefingOut`／`NewsItemOut`对应生成类型，以开工时OpenAPI实际名称为准。`NewsReadingSidebar`接收当天响应的topics、已加载items、早报列表与状态，不自行读管理员settings。
- 正文使用现有 `briefingHtml`／安全renderer；日期使用 `briefingWhen(local_date/slot)`。不把Markdown改成新的AI JSON协议。
- 收藏／不感兴趣仅作用于真实 `NewsItemOut` URL。已加载条目／来源概况基于当前响应，标题写“当前已加载”；不承诺上游全量或AI事件去重数。

- [ ] **Step 1 — RED用例：** 建无标题单段／有两段／无链接正文；断言不强造“3件事”、不把独立资讯的来源贴进摘要、无对应URL的段落没有反馈按钮。对receiving/pending/failed/complete逐一断言，receiving保留parts但不称完成。断言脚本／恶意链接／远程图片被现有安全渲染拦截。
- [ ] **Step 2 — 确认RED：** `npm run test -- tests/news-reading-redesign.test.ts`，保留与新增布局／状态要求对应的失败。
- [ ] **Step 3 — 主阅读区：** 在今天标签重排标题、日期、正文、真实来源和相关资讯；保留往期／热榜／RSS／搜索／收藏六标签、分页、`?briefing=id`与原有管理入口。没有三段就呈现真实正文。
- [ ] **Step 4 — 侧栏与故障：** 展示当前工作空间topics和已加载条目概况、往期入口；owner可去管理，admin按原规则只读，其他角色不调用settings。TrendRadar故障只影响相应资讯区域，已有早报可继续读。去掉没有字段支持的“已合并重复报道”示例。
- [ ] **Step 5 — 副作用与宿主：** 写 `refresh_does_not_generate_push_or_mutate`，点击刷新仅观察GET；`module_disabled_unregisters_and_stops_fetch`验证停用后的导航／路由／widget／SSE宿主行为，无模块请求、无自动启用。窗口切工作空间后不得显示上个空间早报。不能为此添加硬编码全站导航。
- [ ] **Step 6 — GREEN与提交：** 跑新测试与news-pages；按本页变更补必要模块回归。桌面长文阅读／移动排版／亮暗主题／键盘和外链校验通过，记录真实截图与测试后独立提交。本任务不改后端生成、定时任务或模型提示词。

## Task 4：开发者本地聚合与权限核验快照

**Files**
- Modify: `services/api/app/api/v1/developer.py`、`app/domain/developer.py`、`app/schemas/developer.py`、`app/repositories/github.py`
- Modify: `services/api/app/domain/integrations.py`（现有测试连接与凭证／仓库范围／启停更新路径）
- Create: `services/api/app/domain/github_permission_evidence.py`（非敏感快照的保存、失效与安全读取）
- Create: `services/api/tests/test_developer_overview.py`、`test_github_permission_evidence.py`
- Regress: `services/api/tests/test_github.py`、`test_integrations_api.py`、`test_search_api.py`
- Generate: `services/api/openapi.json`、`apps/web/src/services/schema.d.ts`

**Interfaces：总览**
- 新增 `DeveloperService.overview(access: WorkspaceAccess, *, repo_id: UUID | None, q: str | None, range: Literal['24h','7d'], cursor: str | None, limit: int) -> DeveloperOverviewOut`。
- `GET /api/v1/developer/overview`：viewer可读，`repo_id`可选，`q`为仓库名称包含搜索（1–100字符，转义LIKE通配符），`range`默认7d、仅控制失败运行窗，`limit`默认12、范围1–100，`cursor`最大512。无权repo返回404，非法参数422。
- `DeveloperOverviewOut={summary,repos,attention_pulls,failed_runs,recent_updates,sync,partial}`。
- summary字段：`repo_count`、`open_issue_count`、`open_pull_count`、`failed_run_count`，类型均为 `int | null`，另有 `window_start:datetime`、`window_end:datetime`。统计覆盖所选仓库范围全部记录，不受repos分页影响。
- repos为现有Page风格：`items:list[RepoOverviewRow]`、`next_cursor:str|null`、`total_estimate:int|null`。`RepoOverviewRow={repo:RepoOut,open_issue_count:int|null,open_pull_count:int|null,failed_run_count:int|null,latest_run:RunOut|null,partial:bool}`。
- `attention_pulls:list[PullOut]`最多5条：开放PR，优先requested_reviewers非空，再按source_updated_at降序、id稳定排序；不推断审批结果。`failed_runs:list[RunOut]`最多3条。
- `recent_updates`最多5条，类型 `GitHubRecentUpdate={id:UUID,kind:'repo'|'issue'|'pull'|'run',repo_id:UUID|null,title:str,source_url:str,updated_at:datetime,time_basis:'source_updated_at'|'synced_at'}`；没有source_updated_at才使用synced_at并明确“同步于”。不生成虚假活动动词。
- 批量查询本地ExternalResource投影，仅选当前workspace的GitHub integration、有效仓库范围与未tombstone资源；子资源用 `metadata_json.repo_resource_id` 连接。不发外部HTTP，不逐仓库查Issue／PR／run。
- open Issue排除PR；开放PR排除merged与closed。失败仅 `status=completed` 且 `conclusion∈{failure,timed_out}`；按 `source_updated_at ∈ [window_start,window_end)` 归窗。没有该时间的记录不猜结束时间，标partial；界面说明是“近7天更新的失败运行”。同run的当前投影只计一次，不把run_attempt倍增。
- 缺权限／not_found仓库的子数字为null；总汇总中任一所选仓库不能确定某项，则该项为null、partial=true，不拿已知子集冒充全量。其他可读仓库的行与列表可保留。空且已确认的可用范围才返回0。
- sync复用 `SyncState`，rate_limited是reason不是status；unauthorized或not_configured响应无私有记录，统计为null。合法缓存stale保留数据及as_of／retry_at。分页顺序稳定，cursor不得跨workspace／integration／筛选使用。

**Interfaces：逐仓库证据**
- 新增 `DeveloperService.repository_permissions(access: WorkspaceAccess, repo_id: UUID) -> RepositoryPermissionEvidenceOut`，路由 `GET /api/v1/developer/repos/{repo_id}/permissions`，viewer可读安全结果，无权404；不触发测试连接。
- `RepositoryPermissionEvidenceOut={repo_id:UUID,full_name:str|null,checked_at:datetime|null,checks:list[RepositoryPermissionCheck],complete:bool,sync:SyncState}`，复用已有checks枚举 metadata/issues/pull_requests/actions 与 code，不能从Repo.access推导。
- 在既有管理员 `_test_github` 流程将实际返回的RepositoryCheck保存到 `Integration.settings_json['github_permission_evidence']`，含schema_version=1、checked_at、仓库全名和checks；不存Token／Header／原始错误体。每次测试只记录实际执行的结果。
- 当前metadata检查失败会提前终止：缺失的三项表示“未核验”，不是ok或失败；`complete`仅四项都存在时true。无快照为checked_at=null、checks=[]、complete=false。
- Token或仓库清单修改、集成启停／删除时清除快照；GET只服务当前配置范围。unauthorized时不返回旧证据（full_name=null、checked_at=null、checks=[]、complete=false）；限流保留先前合法快照与其旧checked_at，不伪造本次核验完成。若测试中断，仅持久化已经真正得到的结果，不给未执行项造结果。

- [ ] **Step 1 — RED聚合：** 造超过100仓库／多页资源，summary仍为全量；同名跨integration、tombstone、跨workspace排除。验证open与merged区分、failure/timed_out算失败而cancelled/skipped/neutral不算、旧run超窗不计、同run重试不重复。不可访问仓库产生null／partial而非0。验证只有synced_at的更新明确time_basis。
- [ ] **Step 2 — RED证据：** 无快照四项未测；完整检查按四项返回；metadata失败只返回实际一项；Token／仓库范围／启停变化后旧结果不可读；unauthorized清理返回；rate_limited保留旧时间。用禁止HTTP的mock断言两个新GET都不调用GitHub或同步／测试连接。
- [ ] **Step 3 — 确认RED：** `uv run pytest tests/test_developer_overview.py tests/test_github_permission_evidence.py -q`，新契约尚无实现而失败。不得以数据库／认证错误代替RED。
- [ ] **Step 4 — 实现：** 增加上述schema、批量读聚合、路由与快照helper；接入现有管理员测试连接事务和修改集成时的失效。仍只读本地投影；不为页面加PR head_sha同步、审查决策或GitHub写权限。快照写入与原测试审计／幂等／版本更新同事务。
- [ ] **Step 5 — GREEN与契约：** 新测试与test_github／test_integrations_api／test_search_api通过，验证错误响应、分页过滤与安全字段；导出OpenAPI并生成类型。不改历史迁移／第三方API版本。
- [ ] **Step 6 — 提交：** 后端、契约、生成类型、测试和任务状态一起提交，独立PR；前端Task 5从合并后的主干接续。

## Task 5：开发者总览、权限详情和同步状态

**Depends on:** Task 4契约已提供并合并。
**Files**
- Modify: `apps/web/src/pages/DeveloperPage.vue`、`src/features/developer/DeveloperWorkspace.vue`、`queries.ts`、`GitHubRecord.vue`
- Create: `apps/web/src/features/developer/DeveloperOverview.vue`、`RepositoryOverviewTable.vue`、`RepositoryPermissionPanel.vue`、`GitHubAttentionPanel.vue`、`developer.css`
- Create: `apps/web/tests/developer-overview.test.ts`
- Regress: `apps/web/tests/developer-pages.test.ts`、`browser-github-search.cjs`

**Interfaces**
- `queries.ts`新增 `useGitHubOverview(scope: string, filters: MaybeRefOrGetter<DeveloperOverviewFilters>, enabled: MaybeRefOrGetter<boolean> = true)`，其中 `DeveloperOverviewFilters={repo_id?: string;q?: string;range:'24h'|'7d';cursor?: string;limit?: number}`。
- 新增 `useRepositoryPermissions(scope: string, repoId: MaybeRefOrGetter<string | undefined>, enabled: MaybeRefOrGetter<boolean> = true)`；无选中仓库则禁用。两者使用生成类型和现有auth.api／abort signal／developer查询键前缀。
- 表格改变“选中行”只改变权限／详情请求；真正改变范围筛选才更新总览及summary。权限面板失败不让整个总览失败。
- 延续 `githubSync` 的状态优先级；`GitHubRecord`既有分页与source_url保留。所有查询与SSE失效包含新overview／permissions键。

- [ ] **Step 1 — RED用例：** `shows_rate_limit_retry_and_cached_records`断言reason=rate_limited显示“GitHub限流中”、retry_at与as_of；`unauthorized_clears_overview_and_permission_cache`断言重新连接且无旧私有数据；`unknown_checks_never_turn_green_from_repo_access`断言access=ok但无快照仍未核验。
- [ ] **Step 2 — RED关联：** 两仓库同分支、不同commit且仅run有head_sha，PR不得出现“CI通过”；open无review证据只标开放，有requested_reviewers只标已请求审查。failure_summary为空时出现真实缺失提示，不出现硬编码typecheck。快速切仓库／空间时旧权限结果不能串行。
- [ ] **Step 3 — 确认RED：** `npm run test -- tests/developer-overview.test.ts`，新增界面／查询状态未实现而失败。
- [ ] **Step 4 — 实现界面：** 仓库标签使用总览统计、仓库表、开放PR、失败运行与最近更新；保留Issues／PR／Actions原标签、筛选与分页。指标标题为“已同步范围”，失败窗口解释采用Task 4定义；null显示“—”，partial提示不完整。
- [ ] **Step 5 — 实现权限与动作：** 四项检查逐行显示通过／未通过／未核验、code可理解文案、checked_at；测试连接／请求同步只导航现有有权管理入口。刷新只refetch，外链校验后打开；无合并、重跑、Token输入或浏览器直连GitHub。
- [ ] **Step 6 — GREEN与提交：** 新旧developer测试、GitHub搜索浏览器回归及亮暗／手机／桌面截图通过；验证不可用、未配置、限流、失权与正常零记录的不同界面。按实际任务号提交、PR中注明尚不支持每PR CI关联。

## Task 6：运维健康视图与可解释指标

**Files**
- Modify: `apps/web/src/pages/OperationsPage.vue`、`src/features/operations/OperationsWorkspace.vue`、`ServiceList.vue`、`MetricView.vue`、`OperationsNotice.vue`、`StoragePanel.vue`、`queries.ts`
- Create: `apps/web/src/features/operations/ServiceHealthSummary.vue`、`ServiceDetailPanel.vue`、`MetricSeriesChart.vue`、`metricFormat.ts`、`operations.css`
- Create: `apps/web/tests/operations-overview.test.ts`
- Regress: `apps/web/tests/operations-pages.test.ts`、`operations-cards.test.ts`、`browser-operations.cjs`；后端 `services/api/tests/test_operations.py`

**Interfaces**
- 继续 `useServiceStatus(scope, area, enabled)`、`usePresetMetrics(scope, range, views, area, enabled)`；保留 `useOperationsAccessStore` 的失权处理。只消费既有Services／Metrics／Storage生成类型，不新增任意PromQL或服务控制接口。
- `ServiceHealthSummary`接收完整服务数组，计算ok、degraded/down、unknown、not_configured四组；列表按异常优先、名称稳定排序，不写死服务数量。
- `MetricSeriesChart`接收一个 `MetricViewOut`与API的range／step_seconds，按序列独立绘制；提供同数据表。禁止跨序列平均P95。
- `metricFormat.ts`提供 `formatMetricValue(value: number | null, unit: 'requests_per_second'|'seconds'|'ratio'|'count'): string`：null→“—”；1.2 req/s→“1.2 req/s”；0.184秒→“184 ms”；0.012 ratio→“1.2%”；8 count→“8”。仅展示层一次换算，原始数据仍用原单位。
- 现有 `POST /operations/storage/check`仅在有权用户明确点击“执行存储读写自检”后使用，说明会写入、读取并删除探测对象；本期不增加自动自检或新的确认审批流程。

- [ ] **Step 1 — RED服务：** 动态列表含ok/degraded/down/unknown/not_configured以及停用模块，断言四组计数正确、停用服务不算故障；Grafana down但Prometheus ok仍显示指标。无last_success字段不出现“上次成功”。viewer看不到存储／受限服务详情，403后清理旧数据。
- [ ] **Step 2 — RED指标：** 精确验证上述五个格式例子；`[1,null,3]`折线有间断且最新null的当前值是“—”；两个P95序列各自保留label和数值；服务在线count不转百分比。范围从1h迅速切7d后旧响应不覆盖新图，Prometheus错误有缓存stale、无缓存unavailable而非0。
- [ ] **Step 3 — RED副作用：** 进入页面、普通刷新、切范围、定时refetch均断言storage/check调用次数为0；管理员显式一次点击才调用一次；无副本显示“未配置副本”而非健康。grafana_url不存在或非法时无有效跳转。
- [ ] **Step 4 — 确认RED：** `npm run test -- tests/operations-overview.test.ts`，缺少新布局与上述格式／状态行为而失败。
- [ ] **Step 5 — 实现：** 按图重排健康摘要、异常服务表与详情、请求速率／P95等独立图表、存储状态；用现有样式变量和轻量SVG，无新监控后端。指标按API单位与步长显示，未知／无配置／局部失败文案独立；不显示内部URL、原始错误体或凭据。
- [ ] **Step 6 — GREEN与提交：** 新旧operations前端测试、浏览器回归与必要后端现有用例通过；验证390/768/1280/1440/1920、亮暗主题、键盘、数据表替代。存储写自检只用测试实例／mock验证，不能拿生产桶做视觉验收。

## 7. 统一验证与PR交付

以下是**Claude实施时要执行的命令**；本计划交付时没有运行产品测试，不代表检查已经通过。

前端（每个前端交付，在 `apps/web`；先切到Node 22）：

```bash
node --version
npm ci
npm run lint
npm run typecheck
npm run test
npm run check:api
npm run build
```

后端（Task 1/4，在 `services/api`；数据库／Redis使用仓库测试配置，不用生产服务）：

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests scripts
uv run python -m scripts.export_openapi
uv run python -m scripts.export_openapi --check
uv run pytest
```

契约变化后先export、再 `npm run generate:api`、审阅差异并纳入提交；`npm run check:api`使用git diff，应在生成文件已同步到索引／提交后验证零漂移，不能手改类型来凑通过。无后端变化的纯UI任务仍跑check:api，不改无关生成输出。

浏览器脚本按涉及页面执行既有脚本并补新页场景；若依赖测试服务器／浏览器，按脚本当前要求启动并记录URL／数据准备，不能把“页面打开”当成全部验收。资源有限时重型构建／全量测试串行跑。

仓库根目录：

```bash
python3 scripts/collab.py check
git diff --check
```

- [ ] 验收截图：四页各桌面／移动／亮暗，另含知识空空间、早报receiving、GitHub限流、运维缺测／Grafana不可用。示例环境标注测试数据，不把真实隐私放PR。
- [ ] 回归首页#177、全局搜索、角色导航、workspace切换、模块停用，确认局部宽布局未把其他页面撑坏。
- [ ] 每个任务勾选实际完成验收，按协作协议更新done／日志；无法完成保留真实进度、下一步与阻塞，不标假完成。
- [ ] 使用 `[T-实际编号] 简述` 标题，按 `.github/pull_request_template.md` 填目的、最终行为、契约／数据影响、实际命令及退出结果、截图、已知限制。代码和任务记录在同一PR。
- [ ] PR中列出本包与图片的有意差异：早报不虚构三段和去重、PR统计为开放而非待审、不伪造CI关联、无last_success不显示上次成功。
- [ ] 按最新D-0004执行审查与合并规则；部署、不可逆迁移、改协作协议或推进Sprint不由本计划自动授权。

## 8. 完成判定

六项分别可测试、可审查、可合并。最终四页以真实接口驱动，保留原功能与权限，图片中的示例不进入业务；统计、同步、未知值和局部故障均符合Spec。交付必须包含真实验证记录，不以静态样式或mock页面代替完整改造。
