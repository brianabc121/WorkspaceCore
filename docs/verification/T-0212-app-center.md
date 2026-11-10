# T-0212 应用中心改造核验记录（T-0210 后端、T-0211 前端、T-0212 验收）

- 日期：2026-11-10
- 方案：`docs/v2/app-center/`（效果图 `assets/01-app-center.png`、契约、计划、验收矩阵 AC-6）
- 决定：`docs/decisions/2026-11-10-app-center.md`
- 基线：`019a186`（main）；交付分支 `claude/cool-wright-99qbbg`
- 环境：本会话自己的 Docker 守护进程（D-0006）；Python 3.12、Node 22.23.4；专用测试库 `postgresql://…@127.0.0.1:55432`、Redis `127.0.0.1:56379/13|14`（`REQUIRE_INTEGRATION=1`，集成测试没有被跳过）

只记录实际执行过的命令与结果；没有运行的项目在最后一节写明。

## 1. 后端（services/api）

| 命令 | 结果 |
|---|---|
| `ruff check app tests`、`ruff format --check`、`mypy app` | 通过（mypy：270 个源文件无问题） |
| `pytest tests/test_module_management.py` | 9 passed |
| `pytest tests/test_module_sync.py`（连跑 3 次） | 每次 10 passed |
| `pytest tests/test_module_state_migration.py` | 1 passed |
| `pytest tests/test_modules.py tests/test_news_api.py tests/test_news_config.py tests/test_news_migration.py` | 45 passed（改动前后各跑一次） |
| `pytest tests/test_security_matrix.py tests/test_audit_queries.py tests/test_audit_export.py tests/test_operations.py tests/test_navigation_api.py` | 64 passed |
| 全量 `pytest`（`-x`） | 见第 6 节 |
| 迁移：空库 `upgrade head` → `alembic check` → `downgrade 0057` → `upgrade head` → `alembic check` | 通过（专用库 `t0210_mig`） |
| 迁移：已有数据的本地库（0055 → 0058） | 通过，`alembic current` = `0058 (head)` |

覆盖的验收项（方案 AC-6 表）：

- A01 新闻早报真实清单：2 个页面、1 个首页组件、7 个只读工具、1 个定时任务（`每分钟`）；不输出导入路径与设置值（`test_management_projection_is_read_only_and_declarative`）。
- A02 假注册的第二个模块（`news_digest`、`demo`、`demo_child`）自然出现在管理投影与操作记录里，宿主没有硬编码。
- A03 owner 启停 / 重试；admin 只读、可以检测；member 仍能读 `/modules`，管理接口 403；跨工作空间状态与记录隔离。
- A04 / A05 / A06 / A07：缺部署仍可启用（`ready=false`、`not_configured`、不出站）；ready 与健康分开；GET 零探测（计数 TrendRadar 假服务的调用）；检测只调声明的服务，停用 409、没探测器 `not_applicable`、限频 429 带 `Retry-After`。
- A09 / A10 / A13 / A20：钩子抛错 → `failed/hook_failed`，开关不回滚、异常正文不进库也不进审计；超时 → `unknown/timeout`；旧钩子返回 `None` → `unknown/result_unverified`；没有钩子 → `not_required`、重试不写记录；默认启用没有记录 → `unrecorded`。
- A10 中断：遗留的 `running` 超过 60 秒显示 `unknown/interrupted`，重试时先把它改成 `unknown/interrupted` 再执行。
- A11 版本：带过期 `expected_state_version` 409 `VERSION_CONFLICT`（`details.current_state_version`）；旧客户端不带版本照常；相同目标不加版本、不写审计、不执行钩子；旧版本较晚写入的记录不挡住当前目标。
- A12 并发：另一个数据库连接持有 `module-op:<workspace>` 时 PATCH / 重试 409 `MODULE_BUSY` 且没有任何写入；6 轮并发“停用父模块 / 启用子模块”后依赖关系始终成立。
- A14 操作记录：`news` 与 `news_digest`、其他资源类型、其他工作空间不混；游标拿到别的模块上 422。
- A18 真实渲染失败：把配置目录换成普通文件，真实 `write_files` 抛 `OSError` → `failed/config_render_failed`，错误说明写进 `news_settings.render_error`；恢复后重试成功、配置文件是“不推送”。
- A19 资源锁：另一个连接持有 `news-config:<目录>` 时保存设置 409 `NEWS_CONFIG_BUSY`、版本不变，启停照常保存、同步记 `failed/busy`；
  保存设置、加排除词、Worker 重新生成三个写入在停用前开始排队，拿到锁后都按停用后的状态生成，最终配置文件是“不推送”，排队期间的修改都保存了。

## 2. 前端（apps/web）

| 命令 | 结果 |
|---|---|
| `npm run lint` | 通过（0 warning） |
| `npm run typecheck` | 通过 |
| `npx vitest run --maxWorkers=3` | 77 个文件、757 个测试全部通过 |
| `npx vitest run tests/app-center.test.ts tests/approval-center-redesign.test.ts`（改样式前缀后） | 33 passed |
| `npm run build` | 通过 |
| `npm run check:api`（重新生成 `schema.d.ts`） | 没有差异 |

`tests/app-center.test.ts`（15 个）：入口只来自模块声明、对不上不猜路径；声明数量；四类状态与同步失败说明；owner 启停带读到的 `state_version`、停用要确认；
409 与网络中断后重新读取、不重发；admin 只读但能检测、member 被路由守卫挡住；搜索不改汇总；读取失败的错误态；详情与“操作记录”页签各读各的记录；
接入指南没有安装 / 升级 / 清除按钮；自定义页面只改一项并带 version、409 保留草稿确认后合并、移除只改侧栏。

## 3. 真实浏览器（Compose 全栈 + Chromium）

本地用本会话的 Docker 起全栈（镜像构建要访问 PyPI，被本环境的 TLS 代理拦截，所以复用已有镜像并挂载本分支的 `services/api/app`、
`migrations` 与 `apps/web/dist`；CI 会完整构建）。

| 命令 | 结果 |
|---|---|
| `node tests/e2e/modules_browser.cjs`（改成新卡片与确认对话框） | 通过 |
| `node tests/e2e/app_center_browser.cjs`（没有部署 TrendRadar） | 通过：汇总与卡片、打开页面不探测；详情四类状态、检测返回“尚未配置”；停用确认、同步“无需同步”、操作记录、旧版本 409；页签方向键；自定义页面添加 / 别处先保存后确认合并 / 移除；admin 只读、member 没有入口且接口 403；切换工作空间；手机 390 不横向滚动、抽屉 Esc 后焦点回到“查看详情”；暗色 |
| `node tests/e2e/app_center_browser.cjs --deployed`（启动了 TrendRadar 6.11.0 / MCP 4.1.1） | 通过：检测连通正常；`docker compose pause trendradar-mcp` 后检测有界超时、显示连接失败；配置目录改成只读后停用 → 开关已保存、同步失败（外部配置文件没有写成）→ 恢复后重试同步成功、配置文件不推送；再启用同步成功；结束时关掉自己的早报推送 |

截图（桌面 1440、手机 390、暗色、同步失败、导航 409 冲突、重试成功）在本地核对过布局与暗色对比度，不提交进仓库（不含 Key 或内部地址）。
核对中发现并修正：应用中心的样式前缀 `ac-` 与 AI 审批中心重名（会互相覆盖），改为 `apc-`。

CI：`ci-infra.yml` 的 compose job 在 `modules_browser.cjs` 之后运行 `app_center_browser.cjs`；news job 在 `news_acceptance.py` 之后、`news_browser.cjs` 之前运行 `app_center_browser.cjs --deployed`（一个部署同时只能有一个工作空间打开早报推送，`news_browser.cjs` 结束时不关；第一次 CI 把它排在后面，打开推送时 409 `NEWS_TAKEN`，已调整顺序）。

## 4. 安全与数据

- 迁移 0058 只加列（`state_version`，现有行 0）与新表（`module_sync_attempts`，按工作空间级联删除），不改已有数据的含义、不回填历史；可以回退（只在空库或测试库上用）。
- 管理接口不输出 Settings 的值、凭据、导入路径、内部地址或异常正文；同步原因只记固定类别。
- 所有新接口检查工作空间成员与角色（读 owner / admin、检测 owner / admin、重试 owner），写接口带 CSRF。
- 没有新增智能体权限、免审批操作、安装 / 升级 / 清除数据或外部应用协议。

## 5. 没有覆盖的

- 多个 api 进程时，健康缓存是每个进程一份：“最近一次检测”可能因进程而异，页面按 `checked_at` 与 `stale` 显示（ADR 已写明）。
- 本地浏览器验收用的是挂载源码的已有镜像，不是新构建的镜像；以 CI 的完整构建为准。

## 6. 后端全量测试

（见下）
