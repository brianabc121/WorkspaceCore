# ADR：Sprint 2 接口契约——仪表盘、Widget、布局与首页聚合

- 状态：已采纳
- 日期：2026-10-30
- 作者：claude
- 相关：协作任务 T-0014（本契约）、T-0015（后端，claude）、T-0016（前端，codex）、T-0017（验收）；
  `docs/DEVELOPMENT_SPEC.md` 第 5.1 节第 8–9 项、第 6.1 / 6.2 节、第 8.1 ~ 8.3 节、第 13 节；D-0003、D-0005

## 1. 背景与原则

- **机器可读的契约是 `services/api/openapi.json`**；按 D-0005，本契约 PR 同时提交重新生成的 `apps/web/src/services/schema.d.ts`。
  本文说明 OpenAPI 表达不了的约定。
- 端点已全部声明。T-0015 实现之前处理函数返回 `501 NOT_IMPLEMENTED`，但登录、CSRF、工作空间成员与角色校验已经生效。
  **501 不写进 OpenAPI**：实现后契约与前端类型都不需要再变。Sprint 1 端点残留的 501 声明也在本 PR 清除（Sprint 1 契约 ADR 第 1 节）。
- 实现过程中如需修改契约，必须同时更新 `openapi.json`、`schema.d.ts` 和本文，并在 PR 里写明。

## 2. 端点总览

全部是工作空间范围的资源：请求头 **`X-Workspace-Id`** 指定工作空间（Sprint 1 契约第 5 节）。写请求同时要求 CSRF（Sprint 1 契约第 4 节）。

| 方法 | 路径 | 角色 | 成功 | 说明 |
|---|---|---|---|---|
| GET | `/api/v1/dashboard/overview` | viewer | 200 `DashboardOverview` | 首页：默认仪表盘 + 组件 + 模块状态 + 汇总（第 8 节） |
| GET | `/api/v1/dashboards` | viewer | 200 `Page[DashboardOut]` | 默认仪表盘在前，其余按创建时间 |
| POST | `/api/v1/dashboards` | member | 201 `DashboardOut` | 可带 `widgets` 一次创建（导入，第 10 节） |
| PATCH | `/api/v1/dashboards/{dashboard_id}` | member | 200 `DashboardOut` | 改标题、设为首页、保存布局；需要 `version`（第 6 节） |
| GET | `/api/v1/dashboards/{dashboard_id}/widgets` | viewer | 200 `Page[WidgetOut]` | 按桌面端 (y, x) 排序 |
| POST | `/api/v1/dashboards/{dashboard_id}/widgets` | member | 201 `WidgetOut` | 仪表盘版本 +1 |
| PATCH | `/api/v1/widgets/{widget_id}` | member | 200 `WidgetOut` | 改配置；需要 `version`；类型不能改 |
| DELETE | `/api/v1/widgets/{widget_id}` | member | 204 | 仪表盘版本 +1 |

- 路径中的仪表盘 / 组件不属于 `X-Workspace-Id` 指定的工作空间时返回 `404 NOT_FOUND`，与“不存在”无法区分（防 IDOR）。
- 非成员 `404`，viewer 的写请求 `403 FORBIDDEN`（验收场景 A03）。
- `Page` 本 Sprint 不分页（数量有上限，见第 3 节）：`next_cursor` 为 null，`total_estimate` 为条数。

## 3. 数据模型

| 表 | 字段 | 规则 |
|---|---|---|
| `dashboards` | `id`、`workspace_id`、`owner_id`、`title`、`is_default`、`version`、`created_at`、`updated_at` | 工作空间内所有成员共享；每个工作空间**恰好一个**默认仪表盘（部分唯一索引）；`owner_id` 为创建者，用户删除后置空；每个工作空间最多 20 个 |
| `widgets` | `id`、`dashboard_id`、`widget_type`、`config_json`、`layout_json`、`version`、`created_at`、`updated_at` | 随仪表盘级联删除；每个仪表盘最多 50 个；写入前按 Registry 校验 `widget_type` 与配置 |

- **默认仪表盘**：首次安装创建工作空间时一并创建“首页”仪表盘与入门组件；迁移 0004 为已有工作空间补建同样的仪表盘（只新增行，可回退）。
  入门组件：`note`（欢迎文字）、`quick_links`（空）、`todo_summary`、`today_tasks`。
  如果某个工作空间仍然没有默认仪表盘（例如直接写库创建的），首次请求首页聚合时补建（T-0015 实现时确认）。
- 默认仪表盘不能删除（本 Sprint 没有删除仪表盘的端点）；可以用 `PATCH {is_default: true}` 把另一个仪表盘设为首页，原默认自动取消。
- 超出数量上限返回 `409 LIMIT_EXCEEDED`。

## 4. Widget Registry

服务端与前端各维护一份 Registry，**类型清单以本表和 OpenAPI 为准**；前端的 Zod schema 必须与 OpenAPI 中对应的配置模型一致。

| `widget_type` | 用途 | 依赖模块 | 配置 | 建议默认大小（桌面 w×h） |
|---|---|---|---|---|
| `quick_links` | 常用链接 | 无 | `links`：最多 20 条 `{title(1–64), url(http/https，≤2048)}` | 6×3 |
| `note` | 文本便签 | 无 | `text`：≤2000 字符，**按纯文本显示**，不解析 HTML / Markdown | 6×3 |
| `todo_summary` | 待办汇总 | `tasks` | 无字段 | 4×3 |
| `today_tasks` | 今日任务 | `tasks` | `limit`：1–20，默认 5 | 8×4 |

- 写入（创建、修改、导入）：请求体按 `widget_type` 区分（OpenAPI discriminator）；**未知类型、多余字段、取值越界、非 http(s) 链接都返回 `422 VALIDATION_FAILED`**，`details.errors` 只含位置与原因，不回显输入。
- 读取：`widget_type` 是普通字符串、`config` 原样返回——库里可能有以后版本删掉的类型，或前端尚不认识的类型。
  **前端必须用自己的 Registry 再校验 `widget_type` 和 `config`**；不认识或校验失败时显示安全的错误卡片（不渲染配置内容），其余组件照常显示。这就是 DoD“未知 widget type 安全报错”的实现方式。
- `note` 不在规范第 8.2 节的内置清单里，是本 Sprint 新增的本地组件：在还没有外部服务时，用它和 `quick_links` 验证网格、配置与保存。
  `todo_summary`、`today_tasks` 现在就注册，在 Sprint 3 接入 Vikunja 之前显示 `unavailable`，用来验证降级（A05）。
- 新增类型是契约变化（OpenAPI + Registry + 本表）。

## 5. 布局协议

```json
{ "desktop": { "x": 0, "y": 0, "w": 6, "h": 3 }, "mobile": { "y": 0, "h": 3 } }
```

- 桌面端 12 列栅格：`x` 0–11、`w` 1–12 且 `x + w ≤ 12`、`y` 0–999、`h` 1–24。
- 移动端单列（规范 8.1）：按 `y` 从小到大排列，`h` 1–24。
- 服务端只校验取值范围，**不检查重叠**（由前端网格负责排布）；保存什么就原样返回什么，保证“刷新后布局一致”。

## 6. 乐观并发

- `dashboards.version` 在以下任一变化时 +1：标题、默认标记、保存布局、增加 / 删除组件。`widgets.version` 在配置变化时 +1。
- `PATCH /dashboards/{id}` 与 `PATCH /widgets/{id}` 的请求体必须带读取时的 `version`。不一致返回
  `409 VERSION_CONFLICT`，`details.current_version` 为最新版本；**服务端不做自动合并**，前端提示“已被修改”，重新读取后再操作。
- **保存布局**：`PATCH /dashboards/{id}` 的 `layout` 必须**恰好列出该仪表盘当前的全部组件**（每个一次），否则 `422`。
  一次请求在一个事务里更新所有组件的布局，仪表盘版本 +1，组件版本不变（布局不算组件配置）。
- 增加 / 删除组件不需要带版本，但会让仪表盘版本 +1：另一个客户端随后用旧版本保存布局会得到 409，从而不会覆盖别人的改动。
- 验收场景 A04：保存布局后重新读取，布局与保存的一致，仪表盘版本号递增。

## 7. 权限

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看首页、仪表盘、组件 | ✓ | ✓ | ✓ | ✓ |
| 创建仪表盘、保存布局、设为首页、增删改组件 | ✓ | ✓ | ✓ | — |

## 8. 首页聚合 `GET /api/v1/dashboard/overview`（规范 6.2）

```json
{
  "dashboard": { "id": "…", "title": "首页", "is_default": true, "version": 3, "…": "…" },
  "widgets": [ { "id": "…", "widget_type": "note", "config": { "text": "…" }, "layout": { "…": "…" }, "version": 1, "…": "…" } ],
  "summary": { "pending_tasks": null, "active_projects": null, "unread_notifications": null },
  "modules": { "tasks": { "status": "unavailable", "as_of": null, "retryable": false, "reason": "not_configured" } },
  "today_tasks": [],
  "generated_at": "2026-10-30T00:10:00Z"
}
```

- 模块状态：`ok`（数据新鲜）、`stale`（只有旧数据，`as_of` 为其时间）、`unavailable`（拿不到数据）、`unauthorized`（外部授权失效，需要重新连接）。
- `reason`：`not_configured`（尚未接入）、`timeout`、`error` 等，机器可读、不含敏感信息。
- 汇总值未知时为 **null，不以 0 冒充**（A17）；对应模块不是 `ok` 时，`today_tasks` 为空数组且不代表“今天没有任务”。
- 本 Sprint 只有 `tasks` 模块，状态固定为 `unavailable / not_configured`（Sprint 3 接入 Vikunja 后改为读本地投影）。
- **不同步等待外部服务**：各模块读本地投影 / 缓存，每个模块有单独的超时，整个接口总耗时上限 3 秒；
  单个模块出错或超时只把该模块标为 `unavailable`（有旧数据时为 `stale`），接口本身仍然 200（DoD“部分 Widget 失败不影响整页”）。

## 9. Widget 数据协议

组件的渲染输入 = 自己的 `config` + 聚合接口里对应模块的数据与状态：

| `widget_type` | 数据来源 | 状态来源 |
|---|---|---|
| `quick_links`、`note` | 只用 `config` | 始终可用 |
| `todo_summary` | `summary.pending_tasks` | `modules.tasks` |
| `today_tasks` | `today_tasks`（前端按 `config.limit` 截取） | `modules.tasks` |

前端按模块状态显示 loading / empty / unavailable / stale / unauthorized；每个组件独立渲染，一个组件的数据或配置出错不影响其他组件。
本 Sprint 首页只调用一次聚合接口；非默认仪表盘用 `GET /dashboards/{id}/widgets` 获取组件，模块数据仍取自聚合接口。

## 10. 布局导入 / 导出

```json
{
  "format": "my-workspace/dashboard",
  "format_version": 1,
  "title": "我的首页",
  "widgets": [ { "widget_type": "note", "config": { "text": "…" }, "layout": { "desktop": { "x": 0, "y": 0, "w": 6, "h": 3 }, "mobile": { "y": 0, "h": 3 } } } ]
}
```

- 导出在前端完成：取当前仪表盘的标题与组件，**不含** id、版本、时间等字段。
- 导入：前端先校验 `format`、`format_version` 与每个组件（Zod），再 `POST /api/v1/dashboards {title, widgets}`；
  服务端在一个事务里创建仪表盘与全部组件，任何一个组件不合法则整体 422、什么都不创建。导入总是创建新仪表盘，不覆盖现有的；
  需要时再用 `PATCH {is_default: true}` 设为首页。

## 11. 新增错误码

| HTTP | code | 场景 |
|---|---|---|
| 409 | `VERSION_CONFLICT` | `version` 与当前版本不一致；`details.current_version` 为最新版本 |
| 409 | `LIMIT_EXCEEDED` | 仪表盘或组件数量超过上限 |

其余沿用 Sprint 1 契约第 7 节。

## 12. 审计日志

沿用 Sprint 1 契约第 11 节的规则，写入当前工作空间：`dashboard.created`、`dashboard.updated`（metadata 记录变化的字段：`title` / `is_default` / `layout`）、
`widget.created`、`widget.updated`、`widget.deleted`（metadata 含 `widget_type`；不记录 `note` 的文本和链接内容）。

## 13. 不在本契约内

- **全局搜索 UI**（规范 Sprint 2 范围）：本 Sprint 只做前端占位 / 本地搜索（页面导航与当前首页的组件），不新增后端接口；跨资源搜索 API 在后续 Sprint。
- 实时推送（SSE）、仪表盘删除与复制、按用户的私有仪表盘。

## 14. 后果

- 前端可以立即基于 `openapi.json` / `schema.d.ts` 开发；后端按端点替换 501，不改变契约。
- 组件配置在前后端各校验一次：服务端保证存进去的是合法数据，前端保证读到不认识的数据时安全降级。
- 以后新增组件类型（Sprint 3 起的连接器组件）都按第 4 节扩展 Registry，并在第 9 节登记数据来源。
