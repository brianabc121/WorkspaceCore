# Workspace 四页改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.
> 人类已指定交给 Claude 落实，不在当前 Codex 会话启动实施。若 Claude 环境没有这些技能，按下述任务与检查执行等价流程，不为此安装插件。

**Goal:** 将任务、项目、日历、邮件四页落实为统一、真实、可交互的工作界面，其中邮件默认聚合全部邮箱。

**Architecture:** 复用现有 Vue 页面、领域服务和权限边界；先补任务状态与项目统计契约，再逐页改 UI。邮件单独增加服务器聚合层，复用逐账号 Gmail 读写能力，不建立邮件内容镜像。

**Tech Stack:** Vue 3、TypeScript、Vite、Pinia、TanStack Vue Query、Tailwind/shadcn-vue/reka-ui；FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL、Redis、Alembic。前端 Node 22，npm ci。

**Spec:** [四页改造设计规范](../specs/2026-11-08-workspace-four-pages-design.md)。执行者必须同时阅读该文与效果图。
**核对基线:** origin/main c2854e1；未来主干的文件迁移以开工时实际目录为准，不能把本方案当成旧分支恢复指令。

## Global Constraints

- 保留现有技术栈、角色、workspace scope、乐观并发、幂等、任务指派／自由接取、时间轴、模块宿主和动态导航。
- 不纳入首页改造、AI 新闻生成、正式人类验收流程、任务依赖图、自动提醒推送、重复日程或 gmail.modify。
- 新增第一方接口必须先建立契约，再机械生成 services/api/openapi.json 与 apps/web/src/services/schema.d.ts；禁止手改生成文件。
- 邮件 subject/body/snippet/From/To 不写 DB、Redis、浏览器持久化、日志或全局搜索；只在当前视图内存保留。
- 邮件分页状态允许短期存账号 ID、provider pageToken、查询哈希和权限绑定，不包含任何邮件内容或头部。
- 所有未知统计为 null；不把错误、未加载、部分成功映射为 0；所有统计都依据服务端相同过滤器。
- 所有迁移仅新建表／字段／必要约束，历史任务不自动变成待查看；选择最新主干后的新 revision，不占用既有编号。
- 所有效果图数值为示例，不能硬编码进业务逻辑；图中尚不支持的动作按 Spec 的边界隐藏。
- 不覆盖已有未提交工作，不改其他人的进行中任务；不借本计划推进新 Sprint、重写协作协议、重新开启 n8n 或部署。
- 当前本地存在已由 Claude 接手并合并的旧 T-0149 工作残留；不能从旧 codex/T-0149 分支直接开本次实现。

## Review Focus

1. 任务完成轮次变化、同一用户重复标记已查看及另一个用户查看状态：必须无误清、无跨用户影响（Task 1/2）。
2. 聚合邮件的账号内 ID 冲突、快速切账号及迟到响应：不能串正文、附件、回复身份（Task 5/6）。
3. 一个邮箱限流、账号断开、角色撤销及读取范围变化：保留可读邮箱，清掉失权数据；不可泄漏旧游标／缓存（Task 5/6）。
4. 日历 DST、跨日、跨年、超过一页与重叠事件：不漏画、不漂移、不覆盖（Task 4）。
5. 只读 Gmail scope、只读用户和停用模块：不出现可点但不工作的按钮，不绕过权限或模块注册（Task 2–6）。

---

## 0. 开工与交付约定

- [ ] 读取最新 AGENTS.md/docs/AGENTS.md、.collab/decisions/D-0003/D-0004/D-0005、任务与交接；检查本任务是否已被 Claude 或其他执行接手。
- [ ] 在安全的检出中 git fetch origin main；运行 python3 scripts/collab.py inbox --as claude。保护现有改动，从最新主干创建自己的 claude 分支。
- [ ] 按仓库实际未占用编号建立下面六个开发任务；编号须现场分配。由人类明确派给 Claude 的本轮范围按协议记录，不修改他人任务状态。
- [ ] 把本包 docs 与 assets 复制到对应仓库路径，保持相对链接；记录视觉参考与新增状态的缺省决策。此时仍未执行产品开发。
- [ ] 采用任务状态→任务 UI→项目 UI→日历 UI→邮件聚合→邮件 UI 的默认顺序。可独立验收逐 PR 交付，不把 OpenAPI/生成类型拆成互相等待的半成品。
- [ ] 共用文件逐阶段串行修改，尤其 App.vue、style.css、openapi.json、schema.d.ts；若分工，事先固定路径，避免并发覆盖。

以下新增接口和类型是**本方案定义的拟议契约**，需在 Claude 的后端 PR 中实现；现有第三方接口不得照此臆造。

## Task 1：任务状态闭环与项目统计契约

**Files**
- Modify: services/api/app/models/task.py、models/timeline.py、models/__init__.py
- Create: services/api/app/models/task_result_read.py
- Create: services/api/app/domain/task_state.py
- Modify: services/api/app/domain/tasks.py、task_claims.py、task_runs.py
- Modify: services/api/app/repositories/tasks.py、schemas/tasks.py、api/v1/tasks.py
- Create: services/api/migrations/versions/<最新主干下一 revision>_task_work_states.py
- Test: services/api/tests/test_task_work_states.py、test_task_result_view.py、test_project_work_states.py
- Modify: services/api/tests/test_task_claims_api.py、test_timeline_api.py（若开工目录不同，以对应现有文件为准）
- Generate: services/api/openapi.json、apps/web/src/services/schema.d.ts

**Interfaces / data**
- WorkState = todo | running | awaiting_view | blocked | done，判定严格按 Spec §4。
- tasks 新增 completion_seq=0、blocked_at、blocked_reason、blocked_by；task_result_reads 保存当前用户 seen_completion_seq。
- TaskOut 新增 work_state、completion_seq、result_viewed、active_run、blocker。新增属性有兼容默认值；服务端实际返回真实结果，不能依赖默认值伪造状态。
- task_state.py 提供集中状态判定／批量读取；列表过滤、汇总和项目统计共享同一 SQL 条件。不得逐任务查询执行段与已查看记录。
- 保留 GET /api/v1/tasks 的 status=open/done/all，新增可选 work_state、assignee_user_id、assignee_instance_id。执行者两项互斥。新 UI 按状态查看时传 status=all；与旧 status 同时使用时按交集过滤。
- GET /api/v1/tasks/summary：接收与列表相同的非状态过滤器，返回五个互斥状态计数、total、as_of。统计时不套用选中的 work_state，供整排筛选标签使用。
- PUT /api/v1/tasks/{task_id}/result-view：body={completion_seq}，只标记当前用户；重复调用同一轮次幂等。已重开或轮次变化返回 409 COMPLETION_CHANGED，不误标最新结果。
- PATCH /api/v1/tasks/{task_id}/block：body={version, blocked, reason}；任务写权限、CSRF、Idempotency-Key 沿用既有写接口。blocked=true 时非空原因≤2000字符；false 清除原因；返回更新后的 TaskOut。version 过期返回 409。已完成任务设置阻塞返回409；阻塞任务不能被新领取／新启动，解除后才恢复原领取规则。
- GET /api/v1/projects/summary：返回未归档数、需要关注数、指定时间窗的当前已完成任务数；时间窗用 completed_after 含／completed_before 不含。
- ProjectOut 增加 task_states、last_activity_at；现有 open_tasks/done_tasks 口径不变。GET /projects 新增 attention_only，按当前用户状态筛选。
- GET /api/v1/projects/activity：project_id 可选、cursor、limit；返回有范围约束的任务事件，不暴露任意 audit metadata。新增静态路径必须在 /projects/{id}、/tasks/{id} 前注册。

- [ ] **Step 1 — RED:** 新测试覆盖从未开始／已暂停／当前执行／明确阻塞／新完成／个人已查看／重开再完成；两个用户、旧任务 completion_seq=0、冲突 version、跨空间、重复请求和静态路由不被 UUID 捕获。预期新增能力缺失而失败。
- [ ] **Step 2 — 实现:** 写加法迁移与服务端批量状态查询；所有人类/AI 完成入口在同一事务更新 completion_seq、结束执行段、写 completed 动态。本人手动完成可同时写自己的查看记录；重开后的再次完成才加新轮次。必要时扩展 TaskEvent 的 blocked/unblocked CHECK 并迁移，禁止修改历史文件。
- [ ] **Step 3 — GREEN:** uv run pytest tests/test_task_work_states.py tests/test_task_result_view.py tests/test_project_work_states.py -q；再跑原有任务／领取／时间轴回归。比较 5 条与 50 条任务的查询次数，差值应≤2，避免把每行状态改为N+1查询。
- [ ] **Step 4 — 契约/迁移:** 在临时数据库验证 upgrade、旧任务保持、只清空新结构的 downgrade、再 upgrade；执行 export_openapi 与 generate:api/check:api。审查 completion_seq 不被无关编辑增加、阻塞不停止远端进程。
- [ ] **Step 5 — 提交:** 代码、迁移、类型与自己的 .collab 更新同一个 PR；按仓库格式写 feat(tasks): 补任务状态与查看闭环（实际任务号）。

## Task 2：任务页及四页共享视觉基础

**Depends on:** Task 1。
**Files**
- Modify: apps/web/src/App.vue（仅四页宽容器的显式 opt-in）、pages/TasksPage.vue
- Modify: apps/web/src/features/tasks/TasksWorkspace.vue、OperationPanel.vue、shared.ts
- Create: apps/web/src/features/workspace/workspace-pages.css
- Create: apps/web/src/features/tasks/TaskStatusFilters.vue、TaskListTable.vue、TaskDetailPanel.vue、workState.ts
- Test: apps/web/tests/task-workspace-redesign.test.ts；保留 app.test.ts、task-claims.test.ts 等现有回归
- Reuse: components/ui/button、tabs、sheet、dropdown-menu；TaskEditor、TaskReadme、TaskIssues、TaskClaimPanel

**Interfaces**
- workState.ts：WorkState 直接引用生成类型；useTaskWorkCounts(scope, filters)、useTaskResultView(scope)、useTaskBlock(scope) 仅封装 Task 1 的接口。
- TaskStatusFilters 消费服务端计数；选择后改变查询，不对当前分页数组本地凑总数。
- TaskDetailPanel 消费 task_id，发出 close、changed；任务读取、README、执行记录沿用现有能力。
- 选择通过 URL task 表达；搜索／过滤／排序与 cursor 一起更新，改变过滤器重置分页。

- [ ] **Step 1 — RED:** 写测试：状态筛选使用真实参数；过滤后总数仍正确；完成后不会因打开详情自动标为已查看；显式标记后仅当前用户计数减少；轮次冲突重新读取；切空间清掉详情。
- [ ] **Step 2 — 实现:** 按 01-tasks.png 完成列表＋详情；保留新建、编辑、批量／自由领取、README、Issues、看板与时间轴入口。停止／暂停与“标阻塞”分开；不把“处理阻塞”做成假自动修复。
- [ ] **Step 3 — GREEN:** npm test -- tests/task-workspace-redesign.test.ts tests/task-claims.test.ts tests/app.test.ts；原任务交互回归。验证 viewer 可查看／标记自己的查看状态但不能改任务。
- [ ] **Step 4 — 视觉:** 390/768/1280/1440/1920 与暗色截图；详情抽屉 focus trap/ESC/焦点归还；仅四页改变容器宽度，知识库、消息、设置等没有串样式。
- [ ] **Step 5 — 提交:** feat(web): 改造任务列表与详情（实际任务号），包含真实验证记录与前后截图。

## Task 3：项目页卡片、筛选与动态

**Depends on:** Task 1、Task 2 的共享样式。
**Files**
- Modify: apps/web/src/pages/ProjectsPage.vue、features/tasks/ProjectsWorkspace.vue、ProjectOverview.vue
- Create: apps/web/src/features/tasks/ProjectCard.vue、ProjectActivityList.vue、projectQueries.ts
- Test: apps/web/tests/project-workspace-redesign.test.ts、projects-page.test.ts

**Interfaces**
- ProjectCard 使用真实 ProjectOut.task_states、agents、done/open 和 last_activity_at，不在卡片里独立发请求。
- projectQueries.ts 提供 useProjectWorkSummary(scope, filters, completedRange) 与 useProjectActivity(scope, projectId?)；查询键包含当前用户和空间。
- 进度百分比仅由 done/(open+done) 计算；关注条件与 Task 1 的状态计数一致。

- [ ] **Step 1 — RED:** 0任务不为100%；已完成待查看仍计入 done；两个用户关注数可不同；项目分页与筛选一致；没有真实人类成员关系时不显示“我参与”推断。
- [ ] **Step 2 — 实现:** 按 02-projects.png 做响应式网格／列表，卡片进度、真实智能体、风险标记；采用 Spec 中修正后的文案和筛选。最近动态调用已有数据契约，保留项目详情、README、群、Issues。
- [ ] **Step 3 — GREEN:** npm test -- tests/project-workspace-redesign.test.ts tests/projects-page.test.ts；动态失败不遮掉卡片、归档与版本冲突继续可处理。
- [ ] **Step 4 — 视觉/提交:** 各断点、暗色、长项目名、无智能体、空项目、较大列表；feat(web): 改造项目进度与关注视图（实际任务号）。

## Task 4：日历周视图、完整分页与日程详情

**Depends on:** 共享样式；不依赖邮件或任务新状态接口。
**Files**
- Modify: apps/web/src/pages/CalendarPage.vue、features/calendar/CalendarWorkspace.vue、CalendarForm.vue、time.ts
- Create: apps/web/src/features/calendar/WeekTimeGrid.vue、CalendarSidePanel.vue、EventDetails.vue、weekLayout.ts
- Test: apps/web/tests/calendar-week-layout.test.ts、calendar-pages.test.ts
- Browser: apps/web/tests/browser-knowledge-calendar.cjs（必要时添加独立 calendar-redesign 浏览器验收脚本）

**Interfaces**
- weekLayout.ts 提供纯函数 layoutWeekEvents(events, range, zone)，返回按日期裁剪后的起止位置、并列列号、总列数；不改变原始 UTC 数据。
- WeekTimeGrid 消费事件布局与只读任务截止项，发出 select(eventId)、selectTask(taskId)。
- 沿用 /calendar/events、/tasks 的已存在区间与 cursor；两种来源都必须加载后续页，明确加载未完成状态。
- EventDetails 复用 CalendarForm；没有项目字段就不显示项目按钮。任务截止点击去任务，不能走日程编辑 API。

- [ ] **Step 1 — RED:** 固定时区用例验证上海午夜、America/New_York DST、跨年、跨日、全天结束不含、两/三条重叠；121条事件或截止任务不漏后21条。
- [ ] **Step 2 — 实现:** 按 03-calendar.png 做小月历、七天时间画布和侧栏；中小屏改日程列表／详情抽屉；不新增通知渠道或定时推送。
- [ ] **Step 3 — GREEN:** npm test -- tests/calendar-week-layout.test.ts tests/calendar-pages.test.ts；真实创建、编辑、409冲突和 readonly 回归。
- [ ] **Step 4 — 视觉/提交:** 各断点、DST日的23/25小时、长标题和重叠折叠；feat(web): 改造日历周视图与详情（实际任务号）。

## Task 5：多邮箱聚合后端

**Depends on:** 现有 MailService；可独立于 Task 1，但契约文件修改必须串行。
**Files**
- Create: services/api/app/domain/mail_aggregate.py
- Modify: services/api/app/schemas/mail.py、api/v1/mail.py、domain/mail.py（补读取ID/元数据的小粒度复用接口，不改变原search契约）
- Reuse: domain/mail.py、mail_format.py、integrations/gmail.py；只在必要时扩展已核验的读取逻辑
- Test: services/api/tests/test_mail_aggregate.py、test_mail.py、test_mail_tools.py
- Generate: services/api/openapi.json、apps/web/src/services/schema.d.ts

**Interfaces**
- 新增 GET /api/v1/mail/messages；account_id 可选（不填=全部有权读取的账号），q、label、cursor、per_account_limit（默认10，1–20）。
- MailAggregateService.list(access, actor_id, account_id, q, label, cursor, per_account_limit) -> MailAggregatePage。
- MailAggregateItem = MailSummary + account_id + account_email + account_display_name。date 采用 ParsedMessage.internal_date；未知为 null。
- MailAggregatePage：items、accounts（每账号状态/未读数/重试时间）、next_cursor、partial、total_estimate|null。所有响应 no-store。
- 账号状态区分 ok、needs_reconnect、rate_limited、unavailable、pending；部分账号失败时不提供假精确总量。
- 账号worker并发上限3；单账号元数据读取并发上限2，总HTTP并发不超过6。并发worker使用各自AsyncSession/MailService，或将数据库阶段与网络阶段隔离，禁止多个协程共享一个SQLAlchemy AsyncSession。
- 单次外部请求沿用ConnectorHttp的timeout／错误分类／读重试上限，聚合总批次预算12秒。预算结束可返回已读结果，未完成账号标pending；按轮转优先处理尚未尝试的账号。剩余消息ID/位置进入下一游标，不能每次都从最慢账号重头开始。
- 返回一个批次的逐账号结果，客户端归并已加载集合；不承诺 Gmail 未明确保证的跨全历史严格全局排序。
- cursor 为不透明随机句柄，Redis TTL15分钟，仅存用户/空间、账号范围指纹、查询哈希、逐账号 pageToken／尚未读取的消息ID／完成／待处理位置；**不存邮件内容或头部**。输入 cursor 不原地消耗，下一批生成新状态，防请求重试跳过邮件。
- 每次分页重校验用户、空间、账号权限与连接状态；账号集合／查询／分类／limit 改变后旧 cursor 返回422并提示重新加载。过期使用 MAIL_CURSOR_EXPIRED。
- 全部邮箱模式只接受标准系统标签；自定义标签必须绑定单账号，不能把账号A的 Label_123 套到账号B。
- 复用已有账号级详情／会话／附件／发送 API；不新增 Gmail 写 scope，不改变 MCP 的账号分配与读写开关。

- [ ] **Step 1 — RED:** 假 Gmail A/B 各两页；消息ID和会话ID故意相同、时间相同/缺失/乱序，验证复合键与批次稳定归并；翻页不漏账号剩余页，不把provider token混用。
- [ ] **Step 2 — RED:** 增加A限流B正常、全部失败、待重连、跨空间cursor、切用户、变查询、游标过期、权限撤销、重试同cursor和未知总量；至少6账号中前三个慢时，下一轮仍能推进后面账号而不饥饿；真实DB下两个账号同时刷新OAuth避免共享Session错误。
- [ ] **Step 3 — 实现:** 有界并发、每账号结果、无内容的分页状态与范围校验；所有失败时区分网络失败／账号无权／确实为空，不回“正常空列表”。
- [ ] **Step 4 — GREEN:** uv run pytest tests/test_mail_aggregate.py tests/test_mail.py tests/test_mail_tools.py -q；断言Redis/日志没有subject/snippet/body，日志没有q或cursor原文，原逐账号API/MCP行为未被放宽。
- [ ] **Step 5 — 契约/提交:** 生成并检查OpenAPI/类型；feat(mail): 增加多账号聚合读取（实际任务号）。

## Task 6：默认全部邮箱与单邮箱三栏邮件 UI

**Depends on:** Task 5 与共享样式。
**Files**
- Modify: apps/web/src/pages/MailPage.vue、features/mail/MailWorkspace.vue、api.ts
- Create: apps/web/src/features/mail/AccountNavigation.vue、MailMessageList.vue、MailReader.vue、MailComposer.vue、useMailScope.ts
- Test: apps/web/tests/mail-aggregate-pages.test.ts、mail-pages.test.ts
- Browser: apps/web/tests/browser-mail-redesign.cjs（新增，使用合成邮箱与假 Gmail）

**Interfaces**
- useMailScope 初始 accountId=null；无显式账号深链时始终是全部邮箱，不读取“上次第一个邮箱”作为默认。
- 消息选择 SelectedMail={accountId,messageId,threadId}；草稿必含 senderAccountId，回复另含该账号的 replyToMessageId。
- 聚合列表在内存以(account_id,id)去重、按(date降序,account_id,id升序)归并；刷新清空旧cursor从首批重来。
- 搜索、账号和分类变化取消旧请求、清空选择及草稿关联；账号切换时未发送正文先提示保存于当前会话或放弃，不写本地持久化。
- 新建邮件从可发送账号中显式选择；回复默认锁定原信账号。发信前检查账号仍可发送，不因当前列表scope是all而丢失身份。
- 用户离开邮件页／退出／角色或账号授权撤销时取消请求并删除正文查询缓存；权限拦截不能靠隐藏菜单完成。

- [ ] **Step 1 — RED:** 初次打开不选账号即混合显示A/B；点A只看A；返回全部恢复聚合；账号/分类/搜索/刷新范围一致；快速切账号的迟到响应不回填。
- [ ] **Step 2 — RED:** A/B同message_id不串正文附件；回复A只调用A/send，新增发信未选账号不能提交；断开账号和scope变化不能串发；unknown不自动重发。按钮不包括未经支持的归档/删除/标记已读。
- [ ] **Step 3 — 实现:** 按 04/05 两张最终图改三栏；每行和阅读器显示所属账号，部分故障按账号提示，分页改“已加载N封/加载更多”；保留发件记录、结果未知提示、管理邮箱入口。
- [ ] **Step 4 — GREEN:** npm test -- tests/mail-aggregate-pages.test.ts tests/mail-pages.test.ts；浏览器跑完整聚合→单账号→阅读→回复草稿路径，假发送验证所属账号；不实际发送真实邮件。
- [ ] **Step 5 — 视觉/提交:** 亮暗、手机逐级返回、键盘、无邮箱/只读账号/一账号失效；feat(web): 改造统一邮箱与账号切换（实际任务号）。

## 最终验收和交接

**以下是 Claude 需要执行的命令，不是本次已经通过的测试结果。**
前端：Node22，apps/web 工作目录：
~~~
npm ci
npm run lint
npm run typecheck
npm run test
npm run check:api
npm run build
~~~

后端：services/api 工作目录；测试使用临时PostgreSQL/Redis，REQUIRE_INTEGRATION=1，不能因环境缺失静默跳过：
~~~
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests scripts
uv run python -m scripts.export_openapi --check
uv run pytest
~~~

每阶段只跑适合该变更的新增/既有回归；整体交付跑上述完整门槛。迁移升级/回退/再升级仅在临时数据库测试。
若共用2核机器，编译、全前端测试、后端集成套件按顺序运行，避免CPU争用制造超时；不要通过跳过测试掩盖失败。
完成后：
- [ ] python3 scripts/collab.py check、git diff --check；附真实输出、无法运行项和原因。
- [ ] 附四页亮/暗、桌面/手机截图与两种邮件scope截图，逐项对照设计规范§9。
- [ ] 自己任务验收勾选、按协议更新状态；代码与任务同PR，标题使用实际T编号和仓库模板。
- [ ] 若某阶段未完，保持真实in_progress/blocked并写恢复日志，不能把静态效果图或部分实现标成全部完成。
- [ ] 依D-0004处理审查/CI/合并；不因本方案自动部署、越权发信或进行需要人类决定的操作。

本计划已做文档层面的覆盖自查：五类重点风险均对应到任务测试；接口名称、状态名与四页需求已统一。开发、运行验证、迁移和视觉验收仍由Claude实施时完成。
