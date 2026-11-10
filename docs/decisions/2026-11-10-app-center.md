# 应用中心改造：管理投影、状态版本、同步记录与资源锁（T-0210 ~ T-0212）

- 日期：2026-11-10
- 状态：accepted（实现见 T-0210 后端、T-0211 前端、T-0212 验收）
- 方案：`docs/v2/app-center/`（设计、接口与一致性契约、实施计划、验收清单、效果图）

## 背景

原来的应用中心（`/settings/modules`）只有基本卡片与开关：看不到配置入口、外部服务是否连通，模块启停后的外部配置同步
（例如新闻早报重写 TrendRadar 配置文件）失败只记日志；并发启停只靠单个请求事务；零代码页面只能在“侧栏与页面”整页编辑。

## 决定

1. **应用中心是宿主核心功能，不是插拔式模块。** 它管理模块本身，不能被停用；代码在 `services/api/app/modules/`
   （`management.py`、`sync.py`、`locks.py`）与 `apps/web/src/features/app-center/`，不走 `make module-new`。
2. **四类状态分开**：启用（`module.enabled`）、部署（`ready` / `missing`，只看 `.env` 前提是否为空）、
   健康（`health`，来自运维页同一个探测器的缓存）、外部配置同步（`sync`，来自同步记录）。缺部署前提仍可以启用。
3. **读不探测**：`GET /modules/management` 与详情只读注册表、数据库与进程内健康缓存；
   过期的缓存照样显示并标 `stale`。“检测连接”是显式的 `POST /modules/{key}/health-check`：只探测清单声明的服务，
   单个服务最多 10 秒、同时最多 3 个、每个工作空间每个模块每分钟 3 次，模块停用时 409，没部署时不出站。
4. **状态版本**：`workspace_modules.state_version`（迁移 0058，现有行 0）实际启停一次加 1，与清单的发布版本 `version` 无关。
   新页面启停与重试都带 `expected_state_version`，过期 `409 VERSION_CONFLICT`；旧客户端可以不带。
5. **跨进程操作锁**：同一工作空间的启停与重试共用 PostgreSQL 会话级 advisory lock（专用连接，`finally` 里
   `pg_advisory_unlock_all`，失败就作废连接），覆盖“检查依赖 → 写目标 → 提交 → 执行钩子 → 写结果”。拿不到锁 `409 MODULE_BUSY`，
   不写任何东西；锁不挡读。单进程的 `asyncio.Lock` 或随事务提交释放的事务锁都覆盖不了提交后的钩子，所以不用。
6. **同步记录**：`module_sync_attempts` 每次执行一条，先以 `running` 提交再执行钩子；钩子在单独的会话里执行，限时 15 秒；
   结果与审计（`module.sync.succeeded|failed|unknown|retried`）在独立事务里写。只有钩子明确返回 `HookOutcome.applied`
   才算成功；`not_required` 带原因；超时、中断、旧钩子返回 `None` 都是 `unknown`；原因只记固定类别，不存异常正文。
   遗留的 `running` 超过 60 秒显示为中断；版本高的记录优先，旧版本的结果不冒充当前目标。
7. **新闻早报的配置文件写锁**：TrendRadar 配置文件是部署级共享资源，所有写入口（保存设置、加排除词、Worker 重新生成、
   启停钩子、部署命令）共用 `news-config:<配置目录>` 的 advisory lock，拿到锁后再读最新状态（`populate_existing`）。
   锁的顺序：工作空间操作锁 → 部署级资源锁 → 业务行锁；只渲染的入口只拿资源锁。等 10 秒拿不到：页面 409 `NEWS_CONFIG_BUSY`，
   Worker 跳过、下一次再做，钩子记 `failed / busy`。
8. **操作记录**：审计查询加精确的 `resource_id` 条件，`/modules/{key}/activity` 按 `resource_type=module` 与 key 精确过滤，
   游标签名包含 key（换模块旧游标 422）；`/modules/activity` 列出所有模块（含已移除的）。
9. **前端入口来自模块自己的声明**：`ModuleDefinition.management = { primaryPath, settingsPath, category, helpPath }`，
   路径必须是本模块注册过的路由，对不上就不显示按钮；宿主不写任何具体模块的 key。前后端 key 对不上时提示版本不一致。
10. **零代码入口一项一项改、整份带版本保存**：应用中心里添加 / 编辑 / 移除一个入口，在当前角色的完整快照里只改这一项，
    带读到的 `version` 调 `PUT /api/v1/navigation`；409 时保留草稿、读取最新布局，用户确认后把同一个修改套到最新布局再保存。
    移除入口不删除目标；“谁能看到”只决定入口显示，资源仍按原来的权限检查。

## 不做

上传安装包、在线升级、卸载、清除数据、外部应用的接入协议（令牌 / 回调）、运行时导入任意代码、新的智能体免审批权限。
接入指南只说明三种方式，外部应用标“待支持”。

## 影响

- 迁移 0058 只新增列与表，不改已有数据的含义（现有启用记录 `state_version=0`，没有记录的工作空间仍按清单默认值）。
- `ModuleOut` 新增 `state_version`，`PATCH /modules/{key}` 可带 `expected_state_version`；其余接口与行为不变。
- 健康缓存是每个 api 进程一份：多进程部署时不同进程的“最近一次检测”可能不同，页面按 `checked_at` 与 `stale` 显示，不冒充实时结果。
