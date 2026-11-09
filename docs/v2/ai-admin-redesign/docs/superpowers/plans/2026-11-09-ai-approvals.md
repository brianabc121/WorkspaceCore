# AI审批中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 人类指定交给Claude实施。当前Codex只写交接文档；没有相应技能时按等价流程执行，不安装插件来读本计划。

**Goal:** 将AI审批落实为可筛选的待办列表、可核对的固定参数详情与真实执行结果，保持原批准边界。

**Architecture:** 复用AiToolService与ai_tool_executions，仅补读侧汇总、筛选选项、范围和到期排序。批准／拒绝复用原接口、If-Match、参数完整性及身份检查；前端重排为列表＋详情，不新增审批引擎。

**Tech Stack:** Vue 3／TypeScript／TanStack Vue Query及现有UI；FastAPI／Pydantic 2／SQLAlchemy 2。前端Node 22和npm ci。

**Spec:** [八页规范](../specs/2026-11-09-ai-admin-design.md)§2–4、[契约](../specs/2026-11-09-ai-admin-contract.md)§1–2。基线origin/main e0176b59ffcf2ae3e630d4d1b0f75addcf4b1f6c，开工重新fetch。

## Global Constraints

- owner/admin可审批；member/viewer只读自己可见调用，不借审批人权限执行。
- 保留15分钟TTL、每实例待审批上限、固定参数／action_id幂等、critical本人密码和If-Match。
- read/knowledge/direct及现有授权例外不强制审批；无批量批准、永久信任、编辑参数或延长到期接口。
- 默认UI period=24h，汇总按可见全量，不从第一页计数；时间与状态以服务端为准。
- 不泄漏密码／Token／原始消息，来源推断明确；批准和执行结果分开。
- 不手改schema.d.ts，不更新协作协议，不触碰其他在途任务。

## Review Focus

1. 两用户／跨空间与被删实例：汇总、筛选项与详情都遵守_visible（A1/2）。
2. 时钟偏差、到期边界与worker延迟：过期请求不可被UI批准（A1/2）。
3. IM卡片／两个浏览器并发、失权与版本变化：只执行一次，不越权（A2）。
4. 响应丢失、executing／unknown与参数缺失：先核对状态，不盲目重发或编造结果（A2）。
5. direct工具与敏感输入：不误变pending，不泄露密码、来源或不可信HTML（A1/2）。

---

## 开工

- [ ] 读最新AGENTS.md/docs/AGENTS.md、.collab、OC-1契约与OC-5审批ADR及当前工具注册表，检查相同任务／PR与paths占用。
- [ ] git fetch origin main；python3 scripts/collab.py inbox --as claude；按人类指派建／接手实际T编号并start，保护未提交工作。
- [ ] A1可独立先交付兼容后端；A2依赖A1及[管理计划](2026-11-09-admin-settings.md)的B1共享布局。共享生成文件／路由串行修改。

## Task A1：汇总、可见筛选与到期排序

**Files**
- Modify: services/api/app/schemas/ai_tools.py、app/domain/ai_tools.py、app/api/v1/ai.py
- Create: services/api/tests/test_ai_approval_summary.py
- Regress: services/api/tests/test_ai_tools_api.py、test_approval_cards.py、test_oc6_security.py
- Generate: services/api/openapi.json、apps/web/src/services/schema.d.ts

**Interfaces**
- 新AiExecutionSummaryOut／ExecutionInstanceOption及GET /ai/tool-executions/summary，字段按契约§2。
- `AiToolService.summary(access, *, period, instance_id, risks, tool_name) -> AiExecutionSummaryOut`（async）。
- list增加period='all'、sort='created_desc'可选参数；新条件进入cursor绑定。旧调用保持缺省行为，pending_count收紧为可见范围。

- [ ] **Step 1 — RED可见性：** 创建超过一页、分属多个智能体和acting_user的记录，owner/admin汇总全空间、member/viewer仅自己；跨空间无统计／筛选项泄漏。新汇总不是第一页数量。已删实例记录保留、选项不指向他人。
- [ ] **Step 2 — RED时间与分类：** 固定服务端时钟，验证expires_at=now排除、now+180包含、181秒不算即将到期；critical是pending子集；failed/unknown算异常，rejected/expired/denied不算。period24h/7d/all与created_at边界正确，read/direct/knowledge不进入待审批。
- [ ] **Step 3 — 确认RED：** services/api中运行`uv run pytest tests/test_ai_approval_summary.py -q`，缺新契约导致失败，不以数据库未启动充数。
- [ ] **Step 4 — 实现：** 在_visible基础上SQL聚合、生成可见facets，复用现有过期机制，不触发执行。expires_asc稳定排序；summary静态路由放UUID前，游标筛选变化422。
- [ ] **Step 5 — GREEN与契约：** 新测试与原审批／IM／安全回归通过，确认没有改approve/reject或TTL／上限；导出OpenAPI并生成类型，记录真实命令和输出。
- [ ] **Step 6 — 提交：** 代码、契约、生成类型及自己的任务记录同提交／PR；不宣称前端已完成。

## Task A2：审批列表、固定参数与执行结果

**Files**
- Modify: apps/web/src/pages/AiPage.vue、src/features/ai/AiWorkspace.vue、ApprovalActions.vue、ApprovalCard.vue、ExecutionItem.vue、queries.ts
- Create: apps/web/src/features/ai/ApprovalInboxTable.vue、ApprovalDetailPanel.vue、ApprovalSummaryCards.vue、approvalFormat.ts、approval-center.css
- Modify: apps/web/src/app/router.ts（仅/ai显式宽布局；保留/approvals跳转）
- Create: apps/web/tests/approval-center-redesign.test.ts
- Regress: apps/web/tests/ai.test.ts；现有IM卡片／通知浏览器场景

**Interfaces**
- useExecution保留；useExecutions扩展period、sort、instance_id、tool_name及多值risk/status；完整条件加入queryKey。
- `useApprovalSummary(scope, filters)`读取A1，数据与列表用同一用户／空间／非状态条件。
- ApprovalDetailPanel接收executionId；ApprovalActions继续接收scope／完整当前execution，不从可编辑表单重建执行参数。
- 详情用?id=，视图沿?view=扩展异常标签；旧/ai?id和/approvals?id始终可定位列表外记录。

- [ ] **Step 1 — RED界面：** 总计跨页、筛选和到期排序按服务端；倒计时用as_of校时，归零禁用并refetch。来源inferred、参数无快照、未知工具和被删实例如实展示，不编造聊天／AI理由／前后值。
- [ ] **Step 2 — RED决定：** critical空密码不发approve，拒绝无需密码；1–128长度、每次尝试／关闭／换对象清空。If-Match使用当前version，两个窗口／IM卡片同时处理只有一个执行；409/已处理/失权刷新并清不合法内容。
- [ ] **Step 3 — RED未知结果：** approve网络中断先GET，同记录executing/unknown无盲重试；succeeded才显示执行成功。打开／刷新不发approve，参数JSON／summary／来源恶意HTML只显示文本。
- [ ] **Step 4 — 确认RED：** `npm run test -- tests/approval-center-redesign.test.ts`，新增行为断言失败后实施。
- [ ] **Step 5 — 实现：** 使用B1共享外壳、表格／详情／四卡／规则说明；保留现有状态与错误码。活动记录含直接执行，异常区给出真实结果和对应资源核实入口，不增加自动重试／审批规则写入口。
- [ ] **Step 6 — GREEN／浏览器：** 新旧AI测试、IM／通知深链、角色切换、390–1920及亮暗键盘通过；运行README统一质量门槛，记录截图、真实命令及与图片的范围文案调整。
- [ ] **Step 7 — 交付：** 按实际T号与PR模板提交，代码和任务状态同PR；遵守D-0004，未完成留真实进度，不自动部署。
