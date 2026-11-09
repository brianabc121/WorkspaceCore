# 审计查询与导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 人类交给Claude实施；当前Codex只产交接文档，无相应技能时执行等价流程。

**Goal:** 将审计页改为可筛选、可定位详情、可受控导出的工作空间事件记录。

**Architecture:** 新增审计读服务，复用既有只追加日志、scope与脱敏metadata；不改变事件写入与生命周期。筛选在SQL分页前完成，导出同条件的有界记录，前端重排事件表＋详情。

**Tech Stack:** Vue 3／TypeScript／TanStack Vue Query及B1共享布局；FastAPI／Pydantic 2／SQLAlchemy／Python标准CSV。Node 22、npm ci。

**Spec:** [页面规范](../specs/2026-11-09-ai-admin-design.md)§2–3/11、[契约](../specs/2026-11-09-ai-admin-contract.md)§5。基线origin/main e0176b59ffcf2ae3e630d4d1b0f75addcf4b1f6c。

## Global Constraints

- owner/admin，当前workspace，workspace_id=null账户外事件不通过此API暴露。
- 原日志只追加与UPDATE保护不改，不加删除／编辑日志能力。
- metadata继续脱敏，IP只存哈希，不输出ip_hash／原IP；不编造通用结果或前后值。
- actor／对象名称仅在当前权限内补充，当前名不是历史快照；admin不借审计获取owner密钥元数据。
- 导出需31天以内、最多10000行；不静默截断，CSV公式注入防护与no-store，不能导出当前浏览器一页冒充全量。
- 不重构身份／权限，不手改生成类型，不为此建立公共下载服务或新业务表。

## Review Focus

1. 恶意CSV单元格／旧metadata敏感键：下载为文本、凭据不泄漏（C1）。
2. 筛选翻页、时区上下界与10001行：范围准确，超限明确拒绝（C1/2）。
3. 移除成员／对象删除／权限不同：保留原记录，补充名称不越权（C1/2）。
4. 管理员撤权、跨空间或导出错误：不留下前用户文件或旧详情（C1/2）。
5. 未知action／部署已发起／仅新value_version：原样真实，不推导成功与旧值（C1/2）。

---

## 开工

- [ ] 读最新AGENTS.md/docs/AGENTS.md、.collab、审计／安全／权限规则，检查在途paths；fetch origin main、inbox --as claude，记录实际指派并start。
- [ ] C1先后端兼容，C2依赖C1及[六页计划](2026-11-09-admin-settings.md)B1。生成文件、路由、共同样式串行。

## Task C1：审计过滤、详情与CSV

**Files**
- Create: services/api/app/domain/audit_query.py、audit_labels.py
- Modify: services/api/app/repositories/audit.py、app/schemas/audit.py、app/api/v1/audit.py、必要的app/api/deps.py
- Reuse: app/domain/audit.py的redact／AuditTrail；不修改其只追加语义。
- Create: services/api/tests/test_audit_queries.py、test_audit_export.py
- Regress: services/api/tests/test_security.py、test_security_matrix.py与现有审计相关测试。
- Generate: services/api/openapi.json、apps/web/src/services/schema.d.ts

**Interfaces**
- AuditFilters、AuditLogView、AuditExportFile与query服务list/detail/export_csv签名按契约§5。
- 扩展原GET /audit-logs，新增GET /audit-logs/{id}及静态/export（在UUID前）。读方法与现有AuditTrail写方法分离。
- 标签映射未知时回action；资源名批量本地查询、按当前资源可见性，失败降级ID。

- [ ] **Step 1 — RED范围：** seed多空间、多actor及超过一页记录，时间after含/before不含、system_only与actor互斥、action精确／前缀及q在limit前应用；条件不同cursor422，member/viewer403，跨空间详情404，无workspace事件不外露。
- [ ] **Step 2 — RED名称：** 被移出成员退回ID，系统actor=null；当前名称标当前；admin不能补读owner密钥名。未知action原样、部署开始不标完成；metadata只有value_version=3时没有伪造旧值2。
- [ ] **Step 3 — RED导出：** 10000行可导出、10001行422；31天边界、缺／无时区／倒置范围校验；列表过滤与CSV一致。=+-@、前导空白／控制符、引号／逗号／换行用csv解析后仍为文本，密钥／ip_hash及嵌套metadata中的原IP／凭据字段不出现。
- [ ] **Step 4 — 确认RED：** uv run pytest tests/test_audit_queries.py tests/test_audit_export.py -q；新增契约未实现导致失败，不用环境错误替代。
- [ ] **Step 5 — 实现：** 共用SQL过滤、keyset分页与单项scope。文件用同一有界查询结果生成，先检查10001行，不借游标只导一页；复用redact并只输出契约列，固定文件名／BOM／no-store，不生成公开链接。记录export_generated仅安全摘要，排除在本次截止范围之后。
- [ ] **Step 6 — GREEN与提交：** 新旧安全／审计测试通过；检查无日志UPDATE／DELETE、无第三方HTTP、无N+1或绕过权限补名；生成契约与任务记录一起提交。若确需索引另附加法迁移，否则注明无结构变化。

## Task C2：事件表、详情与下载交互

**Files**
- Modify: apps/web/src/pages/AuditPage.vue
- Create: apps/web/src/features/audit/AuditWorkspace.vue、AuditFilters.vue、AuditEventTable.vue、AuditDetailPanel.vue、queries.ts、audit.css
- Modify: apps/web/src/app/router.ts（仅审计页wide opt-in，保留原路径）
- Create: apps/web/tests/audit-center-redesign.test.ts

**Interfaces**
- queryKey包含user/workspace/过滤/cursor，useAuditDetail按ID独立查询；URL保持可分享的非秘密筛选，详情?id=。
- export使用同过滤与scope的受控GET，blob/ObjectURL只在当前下载内存，完成／关闭后revoke；不写localStorage或跨用户复用。
- 当前只有已加载数量时不显示假全量；清除筛选重置cursor，后台新记录不混入前一查询集合。

- [ ] **Step 1 — RED交互：** 查询命中第二页之外记录、后退恢复筛选、单项不在当前页仍可GET；对象／操作者缺失降级，切空间迟到响应不串详情。未知action和原metadata文本不执行HTML。
- [ ] **Step 2 — RED导出：** 导出当前服务端筛选而非当前页，超限／权限错误提示缩小范围或无权；blob失败不报完成，ObjectURL清理，失权／换用户不留下可复用文件。UI用“文件已生成／开始下载”，不假称浏览器已完整接收。
- [ ] **Step 3 — 确认RED：** npm run test -- tests/audit-center-redesign.test.ts。
- [ ] **Step 4 — 实现：** B1外壳、筛选条、事件表、当前名称标记、脱敏详情和导出按钮；无删日志／通用成功率／不可篡改宣传／原IP字段。手机抽屉、键盘与焦点管理完整。
- [ ] **Step 5 — GREEN／交付：** 新旧测试、亮暗／390–1920、导出范围与安全截图、README统一门槛通过；实际T号PR按模板附命令／限制。遵守D-0004，不自动部署。
