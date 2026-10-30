# My Workspace Web

Sprint 3 / T-0022（包含 Sprint 1 认证与 Sprint 2 仪表盘）：Vue 3、TypeScript、Vite、Vue Router、Pinia、TanStack Vue Query、Tailwind CSS。

在本目录使用 Node.js 22（`.nvmrc` 为 22.23.4）和 npm：

```sh
npm ci
npm run generate:api
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

开发服务绑定 127.0.0.1:5173；构建产物位于 `dist/`。根目录 `make dev-web`、`make lint-web`、`make test-web`、`make build-web` 可调用同一组命令。

`/api`、`/healthz`、`/readyz` 在开发模式代理到 `http://localhost:8000`。浏览器访问 `http://localhost:5173` 时，后端的 `CORS_ALLOWED_ORIGINS` 必须包含这个来源；详见认证契约第 4 节。生产服务须为 HTML5 history 路由配置 `index.html` 回退，API 路径须先转发后端。容器和 Caddy 配置由 T-0006 实现。

启动先检查安装状态，再获取当前用户。首次安装创建管理员与默认工作空间；部署管理员提供一次性安装凭据，不提供默认密码。安装完成后使用邮箱和密码登录。

- `/settings`：个人显示名与工作空间名称；member / viewer 只读工作空间名称。
- `/settings/security`：修改密码、会话列表、确认撤销自己的会话；撤销当前会话后回到登录。
- `/settings/audit`：owner / admin 的工作空间审计日志，使用服务端不透明游标分页。
- 未登录访问受保护路由返回登录；连接失败显示可重试状态。401 清空会话与查询缓存；403 / 404 隐藏已经失去访问权限的列表。工作空间切换丢弃旧审计查询，迟到的旧会话响应不会恢复用户或清除新会话。

客户端只请求同源 `/api/v1/...` 路径，使用浏览器 Cookie，不读取会话 Token。每次写请求重新读取 `ws_csrf`（包括改密码换发后的 Cookie）；工作空间资源带 `X-Workspace-Id`。请求超时 15 秒，不自动重试；写入连接中断会提示结果未知，先刷新核实再重试。429 使用 `Retry-After` 提示，统一错误保留 request ID 以供排查，但不直接渲染网关响应正文。

`src/services/schema.d.ts` 由 `../../services/api/openapi.json` 生成，不手改。契约变化后运行 `npm run generate:api` 并提交；`npm run check:api` 与 CI 检查生成结果是否一致。`openapi-fetch` 使用生成的 paths 校验请求与响应类型；Vee Validate + Zod 校验表单，API 数据类型引用生成的 schemas。

新增依赖固定为 openapi-typescript 7.13.0、openapi-fetch 0.17.0、vee-validate / @vee-validate/zod 4.15.1、Zod 3.25.76（对应适配器的 Zod 3 版本）。来源：[OpenAPI TypeScript](https://openapi-ts.dev/introduction)、[OpenAPI Fetch](https://openapi-ts.dev/openapi-fetch/)、[Vee Validate Zod](https://vee-validate.logaretm.com/v4/integrations/zod-schema-validation/)。锁文件固定传递依赖。

任务、项目与外部服务等其他业务页面仍明确标注为规划中，不提供任务、搜索或外部服务连接。主题优先读取本地偏好，再读取系统偏好；仅将主题写入 localStorage，存储不可用时可继续使用。小屏通过顶栏按钮展开导航，跳转后收起。

验证说明见 `VALIDATION.md`；基础栈选择见 `../../docs/decisions/2026-10-30-frontend-stack.md`，接口约定见 `../../docs/decisions/2026-10-30-sprint-1-auth-contract.md`。


## Sprint 2 仪表盘

首页通过一次 `/api/v1/dashboard/overview` 获取默认仪表盘、组件与模块状态。常用链接和纯文本便签独立可用；待办汇总与今日任务在尚未接入任务服务时显示 unavailable，不用 0 或“今天暂无任务”冒充正常数据。未知类型或非法配置只显示错误卡片，不显示原始配置。

- member / admin / owner 可编辑布局：拖动卡片标题、拖动右下角改变大小，或展开“键盘调整布局”填写桌面位置/尺寸与移动端顺序/高度；保存会提交所有组件和读取时的版本。
- viewer 只读。移动端单列，按 mobile.y 排序；桌面拖动只修改 desktop，不覆盖 mobile 排序。
- 409 冲突停止编辑写入，需要成功“重新加载最新版本”后才能继续。网络中断的写入提示结果未知；写入成功但后续读取失败时，明确提示操作已完成，并锁定写入以免重复创建。
- 可添加/配置/确认删除四种组件：note、quick_links、todo_summary、today_tasks。配置编辑器使用对应 JSON schema 校验，便签不解析 HTML，链接仅允许无用户名/密码的 http(s) 地址。
- 导出 JSON 不含 ID、版本和时间；导入先严格校验格式与配置，再一次性创建新仪表盘，不覆盖当前首页。导入后可“设为首页”；也可按需加载并选择工作空间里的仪表盘。设置页提供导入/导出入口。
- `Ctrl/Cmd+K` 打开本地搜索，输入后筛选页面导航与当前已加载首页的组件；不调用尚未提供的外部搜索接口。“创建”和“通知”为明确的禁用占位入口。

GridStack 固定 **14.0.0**（无新增传递运行依赖）。已核验 npm registry 的该版本与包内 `GridStack.init / load / setStatic / change` 类型及实现，使用 12 列、float 模式；DOM 与组件内容由 Vue 管理，不向 GridStack 传入 HTML 配置内容。来源：[npm 14.0.0](https://www.npmjs.com/package/gridstack/v/14.0.0)、[官方文档](https://gridstackjs.com/)、[源码](https://github.com/gridstack/gridstack.js)。按任务路径限制，核验记录放在本文件；需要汇入项目 ADR 时由 Claude 在其文档任务中补录。遵守 D-0005，本任务不改生成类型文件。

### 浏览器冒烟（API mock）

使用已有的 `tests/e2e` Playwright 锁文件安装依赖；不需要真实后端。先在本目录 `npm run dev`，另一个终端：

```sh
# 仓库根目录
npm ci --prefix tests/e2e
# 默认使用 Playwright 下载的 Chromium；首次安装可运行：
npm run browser:install --prefix tests/e2e
cd apps/web
npm run test:browser
# 或使用环境已有浏览器：
CHROMIUM_PATH=/usr/bin/chromium npm run test:browser
```

可用 `WEB_BASE_URL` 指定 Vite 地址，默认 `http://127.0.0.1:5173`。脚本实际执行鼠标拖拽、缩放、带版本的完整布局保存、刷新后读回、CSRF/工作空间头、快捷搜索、移动单列和暗色主题，截图写入 `docs/screenshots/`。这是模拟 API 的前端冒烟；Compose 全栈与 CI 浏览器 E2E 由 Claude 的 T-0017 负责。

截图：[桌面](docs/screenshots/sprint2-desktop.png)、[移动暗色](docs/screenshots/sprint2-mobile-dark.png)。


## Sprint 3 任务与集成

`/tasks` 提供列表/看板、状态/项目/截止时间/优先级/标题筛选和游标分页，任务抽屉支持新建、按变更字段编辑、确认删除。项目与任务都标注 Vikunja 来源，原生链接只允许无凭据的 http(s) 地址；描述只作文本编辑，不解析 HTML。`/projects` 显示投影项目与同步新鲜度。viewer 只读，member 可写任务，owner/admin 配置集成。

`/settings/integrations` 仅向 owner/admin 开放：创建/修改连接、启停、测试连接、手动同步，显示状态、核验版本/实例版本/Token 是否已设置、同步与 Webhook 状态。已有服务地址和 Token 不回填表单、不展示在页面，留空表示保留。Token 不放入 localStorage 或操作队列，提交后清空输入。连接测试的 HTTP 200 也可能有失败检查，界面逐项展示，不把它当作成功。

任务写入每次新提交生成 UUID 幂等键并保存原请求快照。超时、外部服务不可用、网关 5xx 或无效成功响应均按结果待确认处理：锁定新写入，“查询原请求结果”复用原键/方法/请求体，让服务端查询。只有 OPERATION_NOT_APPLIED 且用户确认后才换新键。原请求超出后端 24 小时保留窗口时不再重放，必须核对列表。409 版本冲突先重新读取详情，成功后才能继续；任务已经删除时关闭旧编辑器并释放已确定冲突。

未确认操作只保留在当前页面会话的内存中，按用户和工作空间隔离；关闭抽屉/切换路由或工作空间不会丢掉待确认请求，登出会清理。硬刷新或关闭浏览器会清空内存，因此页面提示先核对列表，不能把刷新后的新表单当作原请求重试。集成写入不具有服务端任务幂等语义，结果未知时只刷新连接状态，不自动重发凭据。任务写入成功和随后的查询刷新分开处理，刷新失败不会诱发重复创建。

首页继续读取真实聚合接口，任务组件按模块 ok/stale/unavailable/unauthorized 显示；今日任务现在显示项目与安全的原生链接。T-0024（PR #28）行号修复在尚未合并时按本任务验收要求带入相同代码和8项回归，不修改该任务文件。

浏览器 API mock 冒烟（沿用上节 Playwright 安装方法，另开终端运行 Vite）：

```sh
CHROMIUM_PATH=/usr/bin/chromium npm run test:browser:sprint3
```

实际流程：列表/看板 → 结果未知的新建 → 关闭抽屉/同键查询 → 版本编辑 → 移动端 → 地址/Token 不回显 → 连接检查失败 → 手动同步。截图在 `docs/screenshots/sprint3-*.png`。后端真实数据、Webhook/对账与全栈浏览器验收由 T-0023 负责；本脚本不宣称全栈验收通过。接口依据 `docs/decisions/2026-10-30-sprint-3-tasks-contract.md` 和已生成类型，没有新增依赖或修改 schema.d.ts。
