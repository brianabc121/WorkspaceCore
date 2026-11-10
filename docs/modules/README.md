# 插拔式模块开发指南

> 给以后新增功能的代理（Claude、Codex）和人看的操作手册。照着做，新功能只加两个文件夹、在注册表里各登记一行，
> 不再去改一堆公共文件；功能在网页“应用中心”里按工作空间启用、停用。
>
> - 方案与取舍：[`docs/v2/MODULES_PLAN.md`](../v2/MODULES_PLAN.md)
> - 架构决定：[`docs/decisions/2026-11-08-feature-modules.md`](../decisions/2026-11-08-feature-modules.md)
> - 参考实现：新闻早报，后端 `services/api/app/modules/news/`，前端 `apps/web/src/modules/news/`

## 1. 先判断：要不要做成模块

| 你要加的东西 | 做法 |
| --- | --- |
| 有自己的页面、数据、接口、智能体工具或定时任务的新功能 | **做成模块**（本指南） |
| 只是一个外部链接、一篇笔记、一个由现有小组件拼成的看板，或想调整侧栏分组 | 不写代码：owner 在“设置 → 侧栏与页面”里新建分组、挪动页面、添加零代码页面（外部链接、站内页面、知识库笔记、仪表盘、嵌入页） |
| 别人写的、独立部署的服务，不想把代码放进仓库 | 外部应用协议（T-0162，等第一个真实需求再做；现在先做成“模块 + 独立 Compose 服务”，像新闻早报和 TrendRadar） |
| 修改任务、项目、知识库等核心功能 | 不属于本指南；改到哪个功能时可以顺手把它迁成模块（第 9 节） |

## 2. 一个模块由什么组成

```
services/api/app/modules/<key>/
  manifest.py      必需：清单（纯数据，见第 3 节）
  api.py           页面接口 router（挂到 /api/v1，路径以 /<key> 开头）；内部接口 internal（可选）
  models.py        ORM 模型（表名以 <key>_ 开头，放在 workspace schema）
  schemas.py       接口的请求 / 响应模型
  service.py       业务逻辑（权限检查、审计、事件都在这里）
  tools.py         智能体工具的 ToolSpec（可选）
  tool_handlers.py 读工具的处理函数（可选）
  jobs.py          定时任务（可选）
  health.py        运维页的外部服务检查（可选）
  sealed.py        需要凭证轮换的加密列（可选）
services/api/tests/test_module_<key>.py（或 test_<key>_*.py）
services/api/migrations/versions/<日期>_<序号>_<key>.py

apps/web/src/modules/<key>/
  index.ts         必需：defineModule({...})（见第 4 节）
  <Name>Page.vue   页面（路由必须按需加载）
  api.ts、组件……
apps/web/tests/<key>*.test.ts
```

注册：后端 `services/api/app/modules/registry.py` 的 `MODULES`、前端 `apps/web/src/modules/registry.ts`，各加一行。
`make module-new` 会自动生成骨架并登记（第 5 节第 2 步）。

## 3. 后端清单字段

清单只写**名字、开关和导入路径**（`"包.模块:属性"`），导入清单不会导入模块其余代码，所以宿主可以在任何地方
安全地读清单而不会循环导入。字段定义在 `services/api/app/modules/base.py`。

| 字段 | 写什么 | 宿主做什么 |
| --- | --- | --- |
| `key` | 小写字母开头，小写字母、数字、下划线，2~32 个字符 | 接口前缀 `/api/v1/<key>`、表名前缀、工具名前缀、事件主题都用它 |
| `name` / `description` / `version` | 中文名、一句话说明、版本 | 显示在应用中心 |
| `default_enabled` | 工作空间没有启用 / 停用记录时是否启用 | 新模块写 `False`；迁移已有功能时为了不改变行为可以写 `True` |
| `depends_on` | 依赖的模块 key | 依赖都启用时才能启用；有启用的模块依赖它时不能停用（409 `MODULE_DEPENDENCY`） |
| `requires` | 部署前提：`Settings` 里的属性名（例如 `trendradar_mcp_url`） | 为空时应用中心显示“需要先部署”和对应的 `.env` 变量名；不阻止启用，模块页面自己显示未部署状态 |
| `models` | 模型所在的 Python 模块 | `app.models` 导入它，Alembic 与测试都能看到这些表 |
| `router` | 页面接口 router | 挂到 `/api/v1`，**每个路由自动加启用检查**（停用时 404 `MODULE_DISABLED`） |
| `internal_router` | 内部接口 router（路径以 `/internal/<key>/` 开头） | 挂到根路径；Caddy 不转发，**模块自己负责认证和启用检查** |
| `secret_path_params` | `{"/internal/<key>/x/{token}": ("{token}",)}` | 访问日志里把这些路径参数换成 `***` |
| `tools` | `ToolSpec` 元组（`make_spec`，名字以 `<key>_` 开头） | 汇总进工具清单；模块停用时不列出、不能调用 |
| `tool_handlers` | 读工具名 → 处理函数 | 汇总进 `READ_HANDLERS` |
| `tools_visible` | `async def visible(db, settings, workspace_id) -> bool` | 模块启用之后还要满足的条件（例如早报还要打开推送） |
| `crons` | `CronSpec("包.模块:函数", {"second": 33, "timeout": 120, "unique": True})` | 登记到 worker；**任务自己只处理启用了模块的工作空间** |
| `event_topics` | 实时事件主题 | 必须已在 `app/schemas/notifications.py` 的 `EventTopic` 里（一致性测试检查） |
| `widgets` | 首页小组件类型 | 必须已在 `app/schemas/dashboards.py` 的组件联合类型里，前端模块也要提供组件 |
| `sealed_fields` | `SealedField` 元组 | 凭证轮换（`python -m app.commands.rotate_credentials`，见 docs/deployment.md）会处理这些加密列 |
| `service_checks` | `ServiceCheckSpec(id, 名称, "包.模块:probe")` | 运维页显示该外部服务能否连通（只在启用了模块的工作空间显示） |
| `on_toggle` | `async def on_toggle(db, settings, runtime, workspace_id, enabled) -> HookOutcome` | owner 启用 / 停用之后、或在应用中心“重试同步”时调用（开关已提交）；用来让外部服务跟着变化，例如新闻早报停用后重新生成 TrendRadar 配置、不再推送。要按数据库里的最新状态协调、可以重复执行；返回 `HookOutcome.applied()` / `.not_required("原因")` / `.failed("原因")`（原因用固定类别，见 `app/modules/sync.py`），只有 `applied` 记为同步成功。宿主给它单独的会话、限时 15 秒，结果写进同步记录，失败不回滚开关（T-0210） |

为什么 `event_topics` 和 `widgets` 还要改宿主文件：它们是 OpenAPI 里的枚举和联合类型，前端据此生成类型，必须静态声明。
模块清单再声明一次，一致性测试保证两边一致。

## 4. 前端模块定义

`apps/web/src/modules/<key>/index.ts` 调用 `defineModule()`（类型在 `apps/web/src/modules/types.ts`，说明见
`apps/web/src/modules/README.md`）：

- `nav`：侧栏条目。`section` 用现有分组（工作空间、连接与工具、管理）或“应用”（新模块默认放这里）；
  `after` 写排在哪一项后面；`minRole: 'admin' | 'owner'` 限制谁能看到和打开；`icon` 用 `@lucide/vue` 的图标；
  模块停用时宿主自动隐藏。owner 还可以在“侧栏与页面”里把它挪到别的分组。
- `routes`：路由，组件必须写成 `() => import('./XxxPage.vue')`（模块不启用就不下载它的代码）。
  模块停用时打开这些地址会看到“功能未启用”页。
- `widgets`：用 `defineWidget({...})` 声明首页小组件（类型、标题、默认大小、zod 配置、按需加载的组件、填写说明）。
- `events`：实时事件主题 → 要刷新的查询键前缀，例如 `{ news: ['news'] }`。
- `management`（可选，T-0211）：应用中心的入口与分类 `{ primaryPath, settingsPath, category, helpPath }`。
  `primaryPath`（“打开应用”）与 `settingsPath`（“应用设置”）必须是本模块 `routes` 里的路径，否则不显示按钮；
  不写就不显示。宿主不按模块 key 写死任何入口。

接口返回 `MODULE_DISABLED` 时，页面和小组件显示“功能已停用”，不要当成普通错误。

## 5. 分步：新增一个模块

1. **建任务**：`python3 scripts/collab.py new --as <你> --owner <你> --title "..." --paths services/api,apps/web`，
   填目标和验收标准；功能有方案的，方案写进 `docs/v2/<KEY>_PLAN.md`。
2. **生成骨架**：`make module-new key=finance name=记账 description=一句话说明`。会生成前后端文件夹、一个
   `GET /api/v1/<key>/status` 接口、一个“应用”分组下的页面和测试，并登记到两个注册表。先跑一遍测试确认骨架是绿的：
   `cd services/api && .venv/bin/pytest -q tests/test_module_<key>.py`。
3. **数据表**：在 `models.py` 写模型（表名 `<key>_` 开头，`ForeignKey("workspace.workspaces.id", ondelete="CASCADE")`），
   在清单里填 `models`；新建迁移 `services/api/migrations/versions/<YYYYMMDD>_<序号>_<key>.py`，`down_revision` 接当前最后一条，
   `schema="workspace"`，并写 downgrade。迁移仍是一条链（不要用 branch_labels）。规范第 4 条：表结构变化必须有迁移和测试。
4. **接口与业务**：每个接口用 `workspace_viewer / workspace_member / workspace_admin / workspace_owner` 依赖做角色检查，
   写操作加 `csrf_protected`；宿主的启用检查不代替角色检查。写操作记审计（动作名 `<key>.xxx`），数据变化后
   `events.mark(db, workspace_id, "<key>")`。调用外部 HTTP 必须用 `ConnectorHttp` / `OutboundPolicy`（超时、错误归类、
   重试上限、防内网访问）。
5. **设置与密钥**：设置放模块自己的表；密钥用 `CredentialBox` 加密后只存引用，接口只回“已设置”，并在 `sealed.py` 声明
   以便凭证轮换。需要用户填的设置做成网页（人类 2026-11-06 要求），部署级的才放 `.env`（`app/core/config.py`，
   变量名以模块名开头）并写进 `docs/deployment.md`。
6. **前端**：在 `apps/web/src/modules/<key>/` 写页面、`api.ts`；改 `index.ts` 的 nav、routes、widgets、events。
7. **智能体工具（可选）**：在 `tools.py` 用 `make_spec` 声明，风险等级照实写——写操作默认要审批，
   **不得为了方便把写工具标成 read 或 direct**（V2 规则：工作台是唯一的权限来源，放宽审批要人类决定）。
   读工具的处理函数放 `tool_handlers.py`。
8. **定时任务 / 外部服务（可选）**：`jobs.py` 的任务先查 `app.modules.service.is_enabled(db, workspace_id, key)`；
   独立部署的外部服务放 `infra/<名字>/compose.yaml`（锁定镜像 digest，不用 latest），在 `scripts/deploy.sh` 加可选组件，
   清单里写 `requires` 和 `service_checks`。
9. **检查**（第 7 节全部命令），**文档**（`docs/api-contract.md` 加接口、`docs/data-dictionary.md` 加表），开 PR。
10. 合并部署后，owner 在“设置 → 应用中心”启用。

## 6. 必须遵守的规则

- **安全**：所有数据读写做工作空间范围 + 角色校验；内部接口自己认证并检查模块启用；Token、Secret、Webhook 内容脱敏；
  路径里的口令在 `secret_path_params` 声明；不得跨工作空间泄露数据。
- **边界**：模块只导入宿主公开的部分（`app.core`、`app.api.deps`、`app.domain` 的服务、`app.models` 的宿主表、
  `app.modules.base` / `app.modules.service`、`app.schemas.tool_spec`）和自己；**不导入其他模块的内部**——确实需要时
  写进 `depends_on`，只用对方公开的函数。宿主代码**不导入模块内部**，只经注册表（例外：`app/commands/` 的部署命令）。
- **命名**：接口 `/api/v1/<key>/...`、内部接口 `/internal/<key>/...`、表 `<key>_*`、工具 `<key>_*`、审计动作 `<key>.*`、
  事件主题 `<key>`。
- **停用**：宿主挡住页面接口、工具、运维检查；内部接口、定时任务、Webhook 接收要模块自己查启用状态。
  停用不删数据，再启用要能恢复。
- **兼容**：清单的 `host_api` 必须等于宿主的 `HOST_API`；宿主扩展点有不兼容变化时加一，并改所有模块。

## 7. 合并前的检查命令

```bash
cd services/api && .venv/bin/ruff check app tests migrations scripts && .venv/bin/ruff format --check app tests migrations scripts
```

```bash
cd services/api && .venv/bin/mypy app migrations tests scripts
```

```bash
cd services/api && .venv/bin/pytest -q tests/test_modules.py tests/test_module_<key>.py
```

```bash
cd services/api && .venv/bin/python -m scripts.export_openapi && .venv/bin/python -m scripts.export_openapi --check
```

```bash
cd apps/web && npm run lint && npm run typecheck && npx vitest run && npm run build && npm run check:api
```

`tests/test_modules.py` 是模块一致性检查（相当于 Home Assistant 的 hassfest）：清单格式、接口前缀、工具名、事件主题、
小组件类型、脱敏路径、每个模块接口在停用时都返回 `MODULE_DISABLED`。全量测试 `make test`（集成测试要设置
`TEST_DATABASE_URL` / `TEST_REDIS_URL`）。

## 8. 停用与移除

- **停用**：owner 在“设置 → 应用中心”关掉；入口、首页小组件、智能体工具、定时任务、外部服务检查都不再生效，数据保留。
- **从代码里移除**：删注册表里的两行和两个文件夹、测试、文档；写一条删表迁移。删表不可逆，按
  `.collab/decisions/D-0004-auto-merge.md` 第 3 节要先问人类。

## 9. 把已有功能迁成模块

参考新闻早报的迁移（T-0160）：

1. `git mv` 把文件搬进 `app/modules/<key>/` 和 `src/modules/<key>/`，改导入；
2. 写清单，把公共文件里的手写登记（路由列表、工具 if 判断、worker 任务、脱敏表、导航和路由映射、小组件注册、事件映射）
   改成从清单来；
3. 为了不改变已有行为，`default_enabled=True`；
4. 现有测试、接口验收、浏览器验收都要照常通过，再加“停用后入口 / 工具 / 任务消失、再启用数据还在”的测试。

## 10. 常见问题

- **循环导入**：清单里只写导入路径字符串；`tools.py` 只导入 `app.schemas.tool_spec` 和自己的 schema，
  不要导入 `app.schemas.ai_tools`。
- **工具没出现**：模块是否启用、`tools_visible` 是否返回 True、实例的权限范围（读写工具要 read_write）。
- **接口一直 404 `MODULE_DISABLED`**：新模块默认停用，先在应用中心（或 `PATCH /api/v1/modules/<key>`）启用。
- **前端类型对不上**：后端改了接口要 `python -m scripts.export_openapi`，前端 `npm run generate:api`。
