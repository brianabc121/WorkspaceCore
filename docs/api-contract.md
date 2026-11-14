# API 契约

> T-0149：n8n 工作流与应用登录账号接口已移除；历史契约中的相关端点、首页模块与工具不再可用，见 `docs/decisions/2026-11-07-remove-n8n.md`。

> 状态：Sprint 1 ~ 5 端点已全部实现；V2（OpenClaw、IM、审批）的端点已在 OC-1 声明，实现中。完整规范见 `docs/DEVELOPMENT_SPEC.md` 第 6 节。
> **机器可读的接口以 `services/api/openapi.json` 为准**（`make openapi` 由代码生成，CI 检查一致性），前端据此生成类型。
> Sprint 1 的 Cookie、CSRF、工作空间上下文、错误码等约定见 `docs/decisions/2026-10-30-sprint-1-auth-contract.md`。
> Sprint 2 的仪表盘、Widget Registry、布局、乐观并发与首页聚合约定见 `docs/decisions/2026-10-30-sprint-2-dashboard-contract.md`。
> 前端类型 `apps/web/src/services/schema.d.ts` 由 claude 随契约一起重新生成并提交（D-0005）。

## 通用约定

- 业务接口前缀 `/api/v1`，`Content-Type: application/json`。
- 每个响应都带 `X-Request-ID` 头。请求可以自带 `X-Request-ID`（1–64 位字母、数字、`.`、`_`、`-`），否则由服务端生成。
- 非生产环境提供 OpenAPI：`/api/v1/openapi.json`，文档页 `/api/v1/docs`；生产环境关闭。

## 统一错误格式

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found", "request_id": "req_...", "details": {}}}
```

| HTTP | code |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `CONFLICT` |
| 422 | `VALIDATION_FAILED`（`details.errors` 只含 `loc`、`msg`、`type`，不回显用户输入） |
| 429 | `RATE_LIMITED` |
| 500 | `INTERNAL_ERROR`（不包含异常细节） |
| 501 | `NOT_IMPLEMENTED`（契约已声明、尚未实现） |

Sprint 1 新增的业务错误码（`INVALID_CREDENTIALS`、`CSRF_FAILED`、`SETUP_COMPLETED` 等）见 Sprint 1 契约 ADR 第 7 节。

## 已实现的接口

### `GET /healthz`

进程存活检查，不检查依赖。

- `200 {"status": "ok"}`

### `GET /readyz`

并发检查 PostgreSQL（`SELECT 1`）与 Redis（`PING`），每项超时 `READINESS_TIMEOUT_SECONDS`（默认 2 秒）。

- 全部正常：`200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`
- 任一失败：`503 {"status": "unavailable", "checks": {"database": "ok", "redis": "error"}}`

失败原因只写入日志（记录异常类型），不出现在响应里。

## Sprint 1 端点

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/setup/status` | 是否需要首次安装 | 已实现（T-0010） |
| POST | `/api/v1/setup` | 首次安装（需安装凭据） | 已实现（T-0010） |
| POST | `/api/v1/auth/login` | 登录 | 已实现（T-0010） |
| POST | `/api/v1/auth/logout` | 登出 | 已实现（T-0010） |
| GET | `/api/v1/me` | 当前用户与所属工作空间 | 已实现（T-0010） |
| PATCH | `/api/v1/me` | 修改显示名 | 已实现（T-0011） |
| GET | `/api/v1/workspaces` | 我所属的工作空间 | 已实现（T-0010） |
| GET / PATCH | `/api/v1/workspaces/{workspace_id}` | 工作空间详情（viewer+）/ 设置（admin+） | 已实现（T-0011） |
| GET / POST | `/api/v1/workspaces/{workspace_id}/members` | 成员列表 / 添加成员（admin+）：已有账号按邮箱加入；没有账号时同时给 `display_name` 与 `password`（初始密码）创建账号，响应头 `X-Account-Created: true`；没给时 404 `USER_NOT_FOUND`；已是成员 409 `MEMBER_EXISTS`；已有账号又给了密码 409 `USER_EXISTS` | 已实现（T-0064） |
| PATCH / DELETE | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 改角色 / 移除：只能是 admin / member / viewer；admin 只管理 member / viewer，涉及 admin（原来是或改成）只能由 owner；owner 不能被修改或移除；全部写审计（`workspace.member_added`、`workspace.member_role_changed`、`workspace.member_removed`、`user.created`） | 已实现（T-0064） |
| GET | `/api/v1/auth/sessions` | 我的会话 | 已实现（T-0011） |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 撤销会话 | 已实现（T-0011） |
| POST | `/api/v1/auth/password` | 修改密码 | 已实现（T-0011） |
| GET | `/api/v1/audit-logs` | 审计日志（需 `X-Workspace-Id`，owner / admin）；筛选、详情与 CSV 导出见“审计查询与导出（T-0197）” | 已实现（T-0011、T-0197） |

请求头约定：写请求带 `X-CSRF-Token`（值取自 `ws_csrf` Cookie）；工作空间范围的请求带 `X-Workspace-Id`。

## Sprint 2 端点

全部需要 `X-Workspace-Id`；viewer 可读，member 及以上可写。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/dashboard/overview` | 首页聚合：默认仪表盘、组件、模块状态与汇总 | 已实现（T-0015） |
| GET / POST | `/api/v1/dashboards` | 仪表盘列表 / 创建（可一并导入组件） | 已实现（T-0015） |
| PATCH | `/api/v1/dashboards/{dashboard_id}` | 改标题、设为首页、保存布局（乐观并发） | 已实现（T-0015） |
| GET / POST | `/api/v1/dashboards/{dashboard_id}/widgets` | 组件列表 / 添加组件 | 已实现（T-0015） |
| PATCH / DELETE | `/api/v1/widgets/{widget_id}` | 修改配置（乐观并发）/ 删除组件 | 已实现（T-0015） |

新增错误码：`409 VERSION_CONFLICT`（`details.current_version`）、`409 LIMIT_EXCEEDED`。

## Sprint 3 端点

约定见 `docs/decisions/2026-10-30-sprint-3-tasks-contract.md`（Vikunja 的核验结果见 `docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`）。
除 Webhook 外都需要 `X-Workspace-Id`；任务的写接口还必须带 `Idempotency-Key`（UUID）。

> **T-0082（2026-11-02）移除了 Vikunja**：项目与任务改为工作台自己的数据，现行接口见下面的「项目与任务（T-0082）」；
> 本节的 Vikunja 部分保留为历史记录。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/integrations` | 集成列表（viewer）/ 新建集成（admin，Token 只写不读；T-0082 起只能是 GitHub） | 已实现（T-0020） |
| PATCH | `/api/v1/integrations/{integration_id}` | 修改集成（admin，乐观并发） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/test` | 测试连接与权限（admin） | 已实现（T-0020） |
| POST | `/api/v1/integrations/{integration_id}/sync` | 排队全量同步（admin，202） | 已实现（T-0021） |
| GET | `/api/v1/projects` | ~~外部项目（投影，带 `sync` 新鲜度）~~ T-0082 改为工作台自己的项目 | 已实现（T-0020，T-0082 改） |
| GET / POST | `/api/v1/tasks` | 任务列表（过滤与分页）/ 创建任务（member，幂等键）；T-0082 起是本地数据 | 已实现（T-0020，T-0082 改） |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改 / 删除（member，幂等键 + `version`）；T-0082 起是本地数据 | 已实现（T-0020，T-0082 改） |
| POST | ~~`/api/v1/webhooks/vikunja/{integration_id}`~~ | Vikunja 回调：只认签名，不用会话与 CSRF（202） | T-0082 已删除 |

新增错误码：`409 IDEMPOTENCY_KEY_REUSED` / `IDEMPOTENCY_IN_PROGRESS` / `OPERATION_NOT_APPLIED` / `INTEGRATION_UNAVAILABLE` / `SYNC_IN_PROGRESS`、
`401 WEBHOOK_SIGNATURE_INVALID`（T-0082 随 Webhook 删除）、`403 EXTERNAL_FORBIDDEN`、`413 PAYLOAD_TOO_LARGE`、`422 EXTERNAL_VALIDATION_FAILED`、
`502 INTEGRATION_UNAUTHORIZED`、`503 EXTERNAL_UNAVAILABLE` / `EXTERNAL_RATE_LIMITED`、`504 RESULT_UNKNOWN`（用同一个幂等键重发以查询结果）。

## 项目与任务（T-0082）

约定见 `docs/decisions/2026-11-02-remove-vikunja.md` 第 3 节（取代 Sprint 3 契约里 Vikunja 的部分）。项目与任务是工作台自己的数据
（单人版：没有负责人、指派、评论）。全部需要 `X-Workspace-Id`；viewer 可读，member 及以上可写，后端校验工作空间范围与角色。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/projects` | 项目列表：默认不含已归档（`include_archived=true` 包含），`q` 按标题搜索，游标分页；每项带 `open_tasks` / `done_tasks` | 已实现（T-0082） |
| POST | `/api/v1/projects` | 新建项目（`title` 最多 120 字，`description` 最多 2000 字）；不需要 Idempotency-Key。owner / admin 可以带 `instance_ids`（一开始加入的智能体，最多 50 个，不能是停用的）和 `im_group: true`（同时建 IM 项目群），member 带这两项返回 403 | 已实现（T-0082，T-0110 加智能体与项目群） |
| GET | `/api/v1/projects/progress` | 首页“项目进度”：未归档项目的未完成 / 已完成任务数（`limit` 最大 20） | 已实现（T-0064，T-0082 改为本地数据） |
| GET / PATCH / DELETE | `/api/v1/projects/{project_id}` | 详情 / 改名、改说明、归档或恢复（带整数 `version`）/ 删除（`?version=`，连同它的全部任务，审计记下删除的任务数） | 已实现（T-0082）；`ProjectOut` 带 `agents`（智能体成员）与 `im_group`（`status` none / pending / ready、`group_id`、`conversation_id`、`error`）；改名、改说明、归档同步到项目群，删除时先解散项目群（T-0110） |
| GET | `/api/v1/projects/{project_id}/agents` | 项目的智能体成员（viewer）：名字、说明、状态、对项目知识库的权限、是否已在项目群里，以及项目群状态 `im_group` | 已实现（T-0110） |
| PUT / DELETE | `/api/v1/projects/{project_id}/agents/{instance_id}` | 让智能体加入 / 移出项目（owner / admin）：同步项目知识库权限（`project_agents`）与项目群成员；重复加入、移出不存在的成员不报错；停用的实例 422 | 已实现（T-0110） |
| POST | `/api/v1/projects/{project_id}/im-group` | 建项目群 / 立即重试（owner / admin）：没有项目群时建一个（群主是自己），正在建的立即重试，已建好的重新同步；没有部署 OpenIM 时 409 `IM_NOT_CONFIGURED` | 已实现（T-0110） |
| PUT | `/api/v1/agents/instances/{instance_id}/project-admin` | 打开或关闭实例的项目管理权限（owner / admin，`{enabled, version}` 乐观并发，审计）：打开后它的工具里多出 `create_project` / `add_to_project` / `list_peers`，直接执行；它建的项目群的群主是打开权限的人；关闭后已建的项目与成员保留。`AgentInstanceOut` 增加 `project_admin`（`enabled`、`granted_by`、`granted_at`） | 已实现（T-0112） |
| GET | `/api/v1/agents/host-summary` | 各实例服务器状态的小指示（owner / admin）：所在服务器 `server_id`；整机的 CPU、内存使用率，两个分区里较高的磁盘使用率，`stale`（超过 3 分钟没上报）与整机正在提醒的告警；这个智能体自己的 `agent_cpu_percent`（占整机的百分比）、`agent_mem_bytes`、`agent_mem_limit` 与 `agent_alerts`；没上报过的实例各项为 null | 已实现（T-0113、T-0146） |
| GET | `/api/v1/agents/instances/{instance_id}/host?range=24h\|7d\|30d` | 实例的服务器状态（owner / admin）：`reported`；`server`（所在服务器的 `id`、`name`，按服务器指纹自动识别或管理员指定）；`current` 整台服务器的当前值（CPU 型号、核心数、使用率、1 / 5 / 15 分钟负载，内存与 Swap，接入包目录分区与 Docker 数据目录分区的总量 / 可用 / 已用百分比，上下行速率 = 这台服务器上各实例之和，开机时长，`stale`）；`points` 整机每小时的平均与峰值；`alerts` 整机告警（`disk_low` / `memory_low` / `load_high` / `not_reporting`）；`agent` 这个智能体自己容器的当前值（`available` 读得到 cgroup、`cpu_cores`、`cpu_percent` 占整机、`mem_bytes` 不含可回收缓存、`mem_limit` 上限或 null、`mem_total` 整机总量、`pids`、`data_bytes` / `data_truncated` 数据目录大小、自己的 `rx_rate` / `tx_rate`、`restarts_1h`）；`agent_points` 它自己每小时的平均与峰值；`agent_alerts`（`mem_near_limit` / `restarting` / `cpu_hog`）。第一次上报和计数器变小（重启）时使用率、核数与网速为 null | 已实现（T-0113、T-0146） |
| PUT | `/api/v1/agents/instances/{instance_id}/limits` | 设置 OpenClaw 容器的资源上限（owner / admin，乐观并发 `version`）：`mem_limit_mb`（256 ~ 1048576 或 null）、`cpus`（0.1 ~ 256 或 null），null 表示不限制（默认，D2）；写进接入包的 `compose.yaml`（`mem_limit` / `cpus`），重新部署这个实例后生效；超出范围 `422`，版本不对 `409`；审计 `agents.instance.limits_updated`。实例详情的 `limits`、`server_id`、`server_manual` 随之返回 | 已实现（T-0145） |
| GET | `/api/v1/agents/instances/{instance_id}/ops` | 实例级运维动作（owner / admin）：`available`（部署到了一台 SSH 服务器、能用那台服务器保存的登录密码登录；否则 `reason` = `not_deployed` / `host_key_unconfirmed` / `credential_missing` / `credential_unreadable`。T-0179 起按保存的密码判断，旧的 `key_missing` 已弃用、不再返回）、这台服务器上进行中的动作 `running`、这个实例最近 20 次记录（7 天内，不含输出） | 已实现（T-0114、T-0145） |
| POST | `/api/v1/agents/instances/{instance_id}/ops` | 对一个实例执行固定动作（owner / admin，202）：`{action, service?, password?}`，`action` ∈ `status` 查看状态 / `logs` 查看日志（`service` ∈ `openclaw` / `tunnel` / `image-proxy`，最近 200 行）/ `report` 立即上报一次 / `restart` 重启 OpenClaw / `up` 重新拉起全部服务；后两个要带自己的**工作台账号**登录密码（二次认证，不是服务器的 SSH 密码；`403 STEP_UP_REQUIRED` / `STEP_UP_FAILED`，错太多次 429）。登录服务器用它保存的登录密码（只用密码、固定主机公钥，T-0179），动作接纳时记下凭证版本，之后密码被替换就失败 `credential_changed`。命令只作用于这个实例的目录与 Compose 项目，不接受命令文本。同一台服务器同时只跑一个动作（`409 AGENT_OPS_RUNNING`），这台服务器有 SSH 任务进行中 `409 AGENT_SSH_JOB_RUNNING`，没部署到 SSH 服务器 `409 AGENT_OPS_UNAVAILABLE`。超时 60 秒，`up` 180 秒。清理镜像改在服务器级（T-0145） | 已实现（T-0114、T-0145） |
| GET / POST | `/api/v1/agents/servers/{server_id}/ops` | 服务器级运维动作（owner / admin）：状态与最近记录 / 执行（`202`，`{action, password?}`）：`containers` 查看这台服务器上所有工作台项目的容器与资源占用（`docker compose ls --all`、`docker ps`、`docker stats --no-stream`、`df`、`uptime`）、`prune` 清理无用镜像（要带自己的**工作台账号**登录密码，二次认证）；登录服务器用它保存的登录密码（T-0179，`reason` 同实例级：`not_deployed` / `host_key_unconfirmed` / `credential_missing` / `credential_unreadable`）；与实例级动作、SSH 任务互斥；`GET .../ops/{action_id}` 取一次动作的脱敏输出 | 已实现（T-0145） |
| GET | `/api/v1/agents/instances/{instance_id}/ops/{action_id}` | 一次动作的状态、退出码与脱敏后的输出（令牌、密钥、`Authorization`、私钥块替换成 `***`，最多 64 KB，`truncated` 表示只保留了末尾）；过期（7 天）后 404 | 已实现（T-0114） |
| GET / POST | `/api/v1/tasks` | 任务列表（`status` = `open` / `done` / `all`、`project_id`、`priority`、`due_before` / `due_after`、`q`、`sort`、游标分页）/ 创建任务（必须带 `Idempotency-Key`） | 已实现（T-0082） |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改（`Idempotency-Key` + `version`；可以改 `project_id` 移到别的项目）/ 删除（`Idempotency-Key` + `?version=`） | 已实现（T-0082） |

- `ProjectOut`：`id`、`title`、`description`、`archived`、`open_tasks`、`done_tasks`、`version`（整数）、`created_at`、`updated_at`。
- `TaskOut`：`id`、`project_id`、`title`、`description`、`done`、`done_at`、`priority`（0–5）、`due_at`、`labels`、`version`（不透明字符串）、`created_at`、`updated_at`。
  去掉了 `integration_id`、`external_id`、`identifier`、`source_url`、`source_updated_at`、`synced_at`；任务列表不再带 `sync`。
- `labels` 最多 20 个、每个最多 50 个字符，重复的只保留一个；修改任务时只有 `due_at` 可以设为 null（清除截止时间）。
- 版本不一致返回 `409 VERSION_CONFLICT`，`details.current` 是最新的项目或任务。
- 任务的幂等在同一个事务里完成：并发的同一个请求返回 `409 IDEMPOTENCY_IN_PROGRESS`（`Retry-After: 1`）；确定的失败（404、409）也记下来，重放时返回同样的错误。
- 首页 `modules.tasks` 总是 `ok`（本地数据）；新工作空间的 `pending_tasks`、`active_projects` 是 0 而不是 null。
- 删除了 `POST /api/v1/webhooks/vikunja/{integration_id}`；集成只剩 GitHub（`provider` 只能是 `github`），`IntegrationCheck.name` 去掉 `tasks_read`。

### 任务时间轴（T-0104）

方案见 `docs/v2/TIMELINE_PLAN.md` 第 2 节，核验见 `docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md` 第 2 节。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| PATCH | `/api/v1/tasks/{task_id}` | 增加 `planned_start_at`、`assignee_user_id`、`assignee_instance_id`（都可以设为 null 清除） | 已实现（T-0104） |
| GET | `/api/v1/tasks/assignees` | 可以指派的执行者：本工作空间的成员（`id`、`display_name`）与没有停用的实例（`id`、`name`、`status`）；viewer。实例接口只给 owner，任务编辑器用这个 | 已实现（T-0104） |
| GET | `/api/v1/tasks/timeline` | `from` / `to`（默认现在前后 12 小时，最长 31 天）、`project_id`、`group` = `executor`（默认）/ `project`；viewer | 已实现（T-0104） |
| PATCH | `/api/v1/tasks/{task_id}` | 增加 `claim_mode`（`assigned` 指派 / `open` 可自由接取）与 `claim_scope`（`all` / `project`），只限 owner / admin（member 带这两项 403）；设为 `open` 时任务必须没有执行者、没有完成（否则 `409 TASK_NOT_OPENABLE`），保存后工作台用你的 IM 账号通知在线、空闲、可写、在范围内的智能体（重放不会重复通知）；设了执行者时自动回到 `assigned`。`TaskOut` 增加 `claim_mode`、`claim_scope`、`claimable` | 已实现（T-0111） |
| GET | `/api/v1/tasks/{task_id}/events` | 任务的动态（viewer）：`opened` 设为自由接取 / `claimed` 领取 / `released` 放弃 / `completed` 完成 / `reopened` 打回，带当时的名字和 `note`（原因或完成说明，按纯文本显示），最多 100 条 | 已实现（T-0111） |
| POST | `/api/v1/tasks/{task_id}/reopen` | 打回（owner / admin）：`{version, reason}`；已完成的任务回到进行中，执行者是智能体时开新的一段并用你的 IM 账号把原因发给它；原执行者停用或删除时放回未分配；没完成 `409 TASK_NOT_DONE`，版本不对 `409 VERSION_CONFLICT` | 已实现（T-0111） |
| GET | `/api/v1/tasks/{task_id}/runs` | 执行记录，最近 100 段，新的在前；带 token、每 10 分钟用量（`series`）与等待审批；viewer | 已实现（T-0104） |
| POST | `/api/v1/tasks/{task_id}/runs` | 开始一段，必须带 `Idempotency-Key`。`{"dispatch": false}`：人的任务开始（member）；`{"dispatch": true}`：交给执行者实例执行（owner / admin） | 已实现（T-0104） |
| POST | `/api/v1/tasks/{task_id}/runs/{run_id}/stop` | 结束一段：`reason` = `paused` / `interrupted` / `cancelled`；已经结束的原样返回；实例执行的段要 owner / admin | 已实现（T-0104） |

- `TaskOut` 增加 `planned_start_at`、`started_at`（第一次开始执行的时间，由执行记录维护，不改 `version`）、`assignee_user_id`、`assignee_instance_id`。
  - 执行者二选一：设了一个，另一个自动清除；两个都给非空值、不是本工作空间的成员 / 实例、实例已停用时返回 422。
  - 新建任务（`TaskCreate`）不变，计划开始与执行者用 PATCH 设：`TaskCreate` 也是智能体 `create_task` 工具的入参，不放宽它能写的字段。
- **一段执行**（`TaskRunOut`）：`executor_kind`（`user` / `instance`）、`user_id` / `instance_id`、`executor_name`（开始时的名字）、`started_at`、`ended_at`、`end_reason`（`done` / `paused` / `interrupted` / `cancelled`）、`started_by`、`ended_by`（系统结束时为空）、`tokens`、`tokens_shared`、`usage_reported`、`waits`、`series`。
  - 同一个任务同时只有一段在执行（`409 RUN_ALREADY_OPEN`）；已完成的任务不能开始（`409 TASK_DONE`）。
  - 人的任务：没有执行者时，点“开始”的人成为执行者。执行者是实例时返回 `409 AGENT_TASK`。
  - 交给实例执行：执行者必须是实例（`409 NO_AGENT_ASSIGNEE`）、没停用（`409 AGENT_DISABLED`）、在线（`409 AGENT_OFFLINE`）。工作台以**当前管理员自己的 IM 账号**发一条固定格式的消息（任务标题、`task_id`、截止时间，请它用 `get_task` 读详情、做完用 `complete_task` 标为完成），发出后才开段。有项目群时，项目群里也发一条通知（T-0111，D6）。
    消息的 Idempotency-Key 由请求的键推出：同一个请求重试不会发两条；IM 结果不确定时返回 `504 RESULT_UNKNOWN`、不开段，用同一个键重试即可。IM 的其他错误照 IM 接口返回（`409 IM_NOT_READY` / `IM_SEND_REJECTED`、`503 IM_UNAVAILABLE`、`429`）。
  - 结束：人点暂停 / 结束；任务标为完成时结束为 `done`（智能体经审批完成也一样）；改执行者时记为 `interrupted`；实例停用时、或离线超过 10 分钟时（Worker 每分钟检查，结束在离线开始的时间）记为 `interrupted`。没有新增 MCP 写工具。
  - **token 按时间归属**：一段用的 token = 这个实例在这段时间里每分钟增量之和；上报有约一分钟延迟，一分钟 m 的增量归给与 [m − 1 分钟, m + 1 分钟) 重叠的段；同一实例同时有几段就平均分（`tokens_shared: true`，界面标“≈”）。人的任务、实例从没上报过时 `tokens` 为 null（`usage_reported: false`，界面显示“未上报”）。
  - `waits`：这个实例的写操作（`write` / `critical`）从提交到有结果的时间，裁到这段的起止；还在等待且这段没结束时 `end` 为 null。
- **时间轴**（`TimelineOut`）：`range_start`、`range_end`、`now`、`group`、`project_id`、`tracks`、`tasks`、`segments`、`usage`、`summary`、`truncated`。
  - `tracks`：按执行者时是实例（看全部项目时包括没有任务的实例，停用的除外）、成员、`former:*`（执行者已不存在，按开始时的名字）、`unassigned`；按项目时每个项目一条。
  - `segments`：`run`（执行段，带 `run`）与 `planned`（没开始、没完成、有计划开始或截止时间的任务：`start` 是计划开始，只有截止时间时为 null；`end` 是截止时间）。超时由前端按任务的 `due_at` 画。
  - `usage`：`bucket_minutes` 自动选，最多 300 桶；`scope = workspace`（看全部项目）是本工作空间所有实例上报的全部 token（含不在任务里的对话），`scope = project` 只算这个项目的执行段归属到的 token；`buckets` 只列有用量的桶。
  - `summary`：`running` 正在执行的段数、`done` 范围内完成的任务数、`tokens`（同 `usage.total.total`）、`overdue` 现在已过截止时间且没完成的任务数；选了项目时都只算这个项目。
  - 执行段最多 2000 段、计划片段最多 1000 个，超过时 `truncated: true`。
- SSE 新增主题 `timeline`：执行记录变化、收到用量上报（有增量时）都会发。

## Sprint 4 端点

约定见 `docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md`。GitHub 与 AFFiNE 的核验结果见
`docs/decisions/2026-10-30-github-rest-and-affine-verification.md`。全部需要 `X-Workspace-Id`。
收藏、知识来源、日程都是本地数据，写入**不需要** `Idempotency-Key`。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/knowledge/sources` | AFFiNE 外部地址（viewer 查看 / admin 配置；每个工作空间 1 个） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/knowledge/sources/{source_id}` | 修改（改地址后收藏的链接随之改）/ 删除（连同全部收藏） | 已实现（T-0027） |
| GET | `/api/v1/knowledge/resources` | 文档收藏列表（私有收藏只返回自己的；可按关联的任务过滤） | 已实现（T-0027） |
| POST | `/api/v1/knowledge/bookmarks` | 收藏 AFFiNE 文档（member；链接只做字符串校验，不访问 AFFiNE） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/knowledge/bookmarks/{bookmark_id}` | 修改（乐观并发；只有创建者能改可见性）/ 删除 | 已实现（T-0027） |
| GET / POST | `/api/v1/calendar/events` | 区间内的日程（`start`、`end` 必填，最多 92 天）/ 新建（定时或全天） | 已实现（T-0027） |
| PATCH / DELETE | `/api/v1/calendar/events/{event_id}` | 修改 / 删除（乐观并发） | 已实现（T-0027） |
| POST | `/api/v1/integrations`（`provider: github`） | 新建 GitHub 集成：细粒度 Token（`github_pat_`）+ 要同步的仓库 | 已实现（T-0028） |
| POST | `/api/v1/integrations/github/repositories` | 列出 Token 能访问的仓库供网页勾选（admin）：新 Token 只用这一次、不保存；不给 Token 时用已保存的 | 已实现（T-0091） |
| GET | `/api/v1/developer/repos`、`/issues`、`/pulls`、`/runs` | GitHub 投影（带 `sync`；限流时 `reason: rate_limited` 与 `retry_at`） | 已实现（T-0028） |
| GET | `/api/v1/developer/overview` | 开发者总览（viewer）：所选仓库范围的已同步统计（仓库 / 开放 Issue / 开放 PR / 时间窗内失败运行，不能确定时为 `null` 并标 `partial`）、仓库概览（分页，游标绑定工作空间、集成与筛选）、开放 PR（已请求审查的在前，不推断审查结果，不关联 CI）、失败运行与最近更新（`time_basis` 标明“更新于 / 同步于”）。只读本地投影，不访问 GitHub、不触发同步或测试连接；Token 被拒 / 未配置时不返回记录 | 已实现（T-0175） |
| GET | `/api/v1/developer/repos/{repo_id}/permissions` | 仓库四项读权限的核验快照（viewer）：管理员上次测试连接时实际得到的结果与 `checked_at`，没有执行的项不出现（未核验），不从 `Repo.access` 推导；Token / 仓库清单 / 启停变化时清除，Token 被拒时不返回 | 已实现（T-0175） |
| GET | `/api/v1/search?q=` | 跨资源搜索（只返回当前用户可见、未删除的资源） | 已实现（T-0029） |

- 保留的聚合接口与组件：
  - `IntegrationOut.repositories`、`IntegrationTestResult.repositories`；
  - `SyncState.retry_at`、`ModuleStatus.retry_at`；
  - 首页 `modules` 增加 `calendar`、`knowledge`、`github`；
  - 组件类型增加 `calendar_agenda`、`recent_docs`、`github_activity`。
- 修改集成时，`api_token` 的前缀必须与 provider 一致；`repositories` 只用于 GitHub。不适用时返回 422（已实现）。
- 新增错误码：`409 BOOKMARK_EXISTS`（`details.existing_id`）。收藏链接不合规时返回 `422 VALIDATION_FAILED`，`details.reason` 见契约第 4.2 节。

## Sprint 5 端点

约定见 `docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md`。Prometheus、Grafana 的核验结果见
`docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`。除 SSE 外都需要 `X-Workspace-Id`。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/operations/storage` | 知识库图片存储（owner / admin）：本工作空间的主存储、副本（名字、是否配置完整与缺少的字段、R2 / OSS 的桶地址、最近一次自检）、图片数 / 用量 / 配额、待复制与复制失败的数量；不返回密钥，本地存储不返回路径 | 已实现（T-0103；按工作空间 T-0223） |
| GET | `/api/v1/settings/storage` | 图片存储设置（owner / admin，`Cache-Control: no-store`）：`primary`、`replica`、`r2`（账户 ID、桶名、辖区、Access Key ID、`secret_set`）、`oss`（地域、桶名、AccessKey ID、`secret_set`）、`in_use`（还存着本工作空间图片或快照的存储）、`version`、`updated_at`；不返回密钥 | 已实现（T-0223） |
| PUT | `/api/v1/settings/storage` | 保存（owner，CSRF，乐观并发 `version` → `409 VERSION_CONFLICT`）：密钥 `null` 保持、`""` 清除；账户 ID / 辖区 / 地域 / 桶名只能是小写字母、数字和短横线（地址只由它们拼出 R2 / OSS 的官方域名）→ `422 VALIDATION_FAILED`（`details.errors[].loc`）；选了的存储要填完整；`in_use` 的存储不能清空 → `409 STORAGE_IN_USE`；保存前对主存储与副本各写、读、删一个探测对象，不通过 → `422 STORAGE_CHECK_FAILED`（`details.checks`），设置不变；成功返回设置与 `checks`，审计 `storage.settings_updated`（只记改了哪些字段、哪个存储的密钥换了） | 已实现（T-0223） |
| POST | `/api/v1/operations/storage/check` | 存储自检（owner / admin）：对主存储与副本各写、读、删一个探测对象，结果存进 Redis 并返回最新的 `StorageStatus`；Worker 启动时和每天也自检一次 | 已实现（T-0103） |
| GET | `/api/v1/operations/services` | 核心服务、监控组件与集成的状态（没有部署的为 `not_configured`）；V2 另有 `kind = agents` 的 OpenIM 与本工作空间的 OpenClaw 实例（实例只对 owner / admin 列出） | 已实现（T-0035；V2 部分 T-0062） |
| GET | `/api/v1/operations/metrics?range=&views=` | 预设指标视图，聚合好的数据点；不接受 PromQL；带 `sync` 与 `grafana_url` | 已实现（T-0035） |
| GET / PATCH | `/api/v1/notifications` | 我的通知（游标分页、`unread_count`）/ 标为已读（`ids` 或 `all: true`） | 已实现（T-0036） |
| GET | `/api/v1/events/stream?workspace_id=` | SSE：只发 `event: invalidate` 失效提示；`Last-Event-ID` 续传，无法续传时 `reset: true`；连接太多时 `429` | 已实现（T-0036） |

- 保留的聚合接口与组件：
  - 首页 `modules` 增加 `operations`；`summary.unread_notifications` 从 T-0036 起返回实际值；
  - 组件类型增加 `service_health`、`api_latency`、`notifications`；
- SSE 的事件类型在前端用 `components["schemas"]["InvalidateEvent"]`；开始推送之前的错误是普通 JSON。

## V2 端点（OpenClaw 实例、密钥库、IM、工具审批）

约定见 `docs/decisions/2026-10-31-v2-oc1-contract.md`（下称“V2 契约”）；上游核验见
`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`。标为“已声明”的端点目前返回 `501`，由括号里的任务实现。

**管理员接口**（浏览器；会话 + CSRF + `X-Workspace-Id`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / PATCH | `/api/v1/agents/settings` | 实例总开关（owner） | 已实现（T-0054） |
| GET / POST | `/api/v1/agents/instances` | 实例列表 / 登记（owner；登记不签发凭证） | 已实现（T-0054） |
| GET / PATCH | `/api/v1/agents/instances/{instance_id}` | 详情 / 修改（乐观并发；网络模式不能改） | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/disable`、`/enable` | 停用（凭证失效、IM 强制下线）/ 启用 | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/bundle` | 生成接入包（`application/zip`）并签发新凭证；凭证只在这里出现一次 | 已实现（T-0054） |
| POST | `/api/v1/agents/instances/{instance_id}/credentials/{credential_id}/revoke` | 立即吊销一份凭证 | 已实现（T-0054） |
| PUT | `/api/v1/agents/instances/{instance_id}/secret-grants` | 实例可取用的密钥（整体替换） | 已实现（T-0054） |
| GET / POST | `/api/v1/agents/servers` | OpenClaw 服务器（owner，T-0145 / T-0147，`docs/v2/MULTI_OPENCLAW_PLAN.md` 第 7 节；登录密码见 `docs/decisions/2026-11-09-agent-server-password-auth.md`）：列表（每台服务器的类型 `kind` = `ssh` 管理员添加 / `auto` 按上报的服务器指纹自动识别、`ssh` 信息（`auth_method=password`、`password_set`、`credential_revision`、`connection_status`、`last_connection_at`、`legacy_key_cleanup_state`；不返回密码与密文引用）、整机 CPU / 内存 / 磁盘 / 15 分钟负载、`instances_total` / `instances_online`、`alerts`、`initialization_status`（auto 为 null）、`deployable` / `deploy_block_reason`、`managed_deployments`、`successful_deployments`（有运行状态未知的旧记录时 null）、`deployment_counts_partial`、`allocated_mem_limit_mb` / `allocated_cpus` / `has_unlimited_limits`，全量统计 `summary`，以及还没归属的实例 `unassigned`）/ 添加一台 SSH 服务器（`201`，`{name?, host, port=22, username, login_password}`，名称省略用 `host:port`）：只解析地址、读取主机公钥（不登录），返回 SHA256 指纹，登录密码加密保存（指纹确认前不发给服务器）；可选 `Idempotency-Key`；每个用户每分钟最多探测 10 次（`429`），连不上 `503 AGENT_SSH_UNREACHABLE`，同一地址端口账号 `409 AGENT_SERVER_EXISTS`；审计 `agents.server.created` | 已实现（T-0145；密码与统计 T-0178） |
| GET / PATCH / DELETE | `/api/v1/agents/servers/{server_id}` | 详情（`?range=24h\|7d\|30d`）：`current` 整机当前值、`points` 整机每小时趋势、`agents` 每个智能体一行（`deployed`、`deployment_state` = `associated` / `deploying` / `deployed` / `failed` / `interrupted` / `retained`、`latest_job`、`deployment_project`（Compose 项目名，不是容器名）、`install_dir`、`load` 自己的用量、`alerts`、`tokens_1h`、`running_task`）、`job` 这台服务器最近一次 SSH 任务 / 改名称（乐观并发）/ 移除（`?version=`，`204`）：SSH 服务器要先卸载上面的实例（`409 AGENT_SERVER_HAS_DEPLOYMENTS`）、没有进行中的任务（`409 AGENT_SSH_JOB_RUNNING`）；旧方案的专用公钥还没退役（`pending`）时 `409 AGENT_SERVER_LEGACY_KEY_PENDING`，先补录密码并完成旧公钥退役（退役失败记为 `manual_required` 后可以移除，那一行由 owner 手动删除）；不删容器、不改 sshd；自动识别的直接移除，上面的实例改为未归属 | 已实现（T-0145；密码与旧公钥规则 T-0179） |
| PUT | `/api/v1/agents/servers/{server_id}/address` | 更换地址或账号（连同 `login_password`，必填）：重新读取主机公钥，要重新确认，初始化与连接状态清空；有部署记录 `409 AGENT_SERVER_HAS_DEPLOYMENTS`；同机有任务 `409 AGENT_SSH_JOB_RUNNING`；旧方案的专用公钥还没退役时不拒绝：那一行留在原机器上，工作台清掉旧引用、`legacy_key_cleanup_state` 记为 `manual_required`，`warning` 给出要手动删除的那一行（T-0179）；可选 `Idempotency-Key` | 已实现（T-0145；密码与部署检查 T-0178；旧公钥 T-0179） |
| PUT | `/api/v1/agents/servers/{server_id}/credentials` | 替换登录密码（`{version, login_password}`）：加密保存、`credential_revision` + 1、`connection_status` 回到 `unknown`；保留 `prepared_at` 与实例；同机有 SSH 任务或运维动作 `409 AGENT_SSH_JOB_RUNNING`（原密码不变），版本过期 `409 VERSION_CONFLICT`；可选 `Idempotency-Key`；审计 `agents.server.credentials_set`（不含密码） | 已实现（T-0178） |
| POST | `/api/v1/agents/servers/{server_id}/test-connection` | 测试连接（`{version}`）：按确认过的主机公钥、只用保存的密码登录（不尝试公钥 / ssh-agent），只读检查系统、Docker / Compose / sudo（需要时同一个密码经标准输入验证 sudo）；登录结果写进 `ssh.connection_status`（`connected` / `auth_failed` / `unreachable` / `host_key_changed` / `credential_unreadable`）、返回 `200`，环境问题在 `warning`；不装 Docker、不建容器、不写 authorized_keys。没确认指纹 `409 AGENT_SSH_HOST_KEY_UNCONFIRMED`，没保存密码 `409 AGENT_SERVER_CREDENTIAL_REQUIRED`；与探测共用限流（`429`）；审计 `agents.server.connection_tested` | 已实现（T-0178） |
| POST | `/api/v1/agents/servers/{server_id}/legacy-key-cleanup` | 旧专用公钥退役（`{version}`，`200`）：用保存的登录密码登录（只用密码，绝不用旧私钥），从 `~/.ssh/authorized_keys` 只删本记录那一把公钥（按类型与公钥数据匹配；别的用户、运行服务器的公钥保留），成功后清空旧私钥引用与公钥、`legacy_key_cleanup_state=done`，审计 `agents.server.legacy_key_cleaned`。服务器上失败（连不上、登录被拒、主机公钥变化、写不进）仍返回 `200`：保留旧引用、记为 `manual_required`，`warning` 里是原因与要手动删除的那一行，可以重试，审计 `agents.server.legacy_key_cleanup_failed`。没有旧公钥 `409 AGENT_SERVER_NO_LEGACY_KEY`；没保存密码 / 解不开 `409 AGENT_SERVER_CREDENTIAL_REQUIRED` / `AGENT_SERVER_CREDENTIAL_UNREADABLE`；没确认指纹 `409 AGENT_SSH_HOST_KEY_UNCONFIRMED`；同机有任务 `409 AGENT_SSH_JOB_RUNNING`；与测试连接共用限流（`429`）。登录与改文件期间持有这台服务器的行锁（总时限 60 秒） | 已实现（T-0179） |
| POST | `/api/v1/agents/servers/{server_id}/confirm` | 确认主机公钥的指纹（与探测结果不一致时 `409 AGENT_SSH_FINGERPRINT_MISMATCH`），之后只接受这把公钥 | 已实现（T-0145） |
| POST | `/api/v1/agents/servers/{server_id}/prepare` | 初始化服务器（`202`，任务 `kind=prepare`，`{version}`，T-0178 起不再收密码）：用保存的登录密码（没保存 `409 AGENT_SERVER_CREDENTIAL_REQUIRED`，解不开 `409 AGENT_SERVER_CREDENTIAL_UNREADABLE`，没确认指纹 `409 AGENT_SSH_HOST_KEY_UNCONFIRMED`）。T-0179 起任务全程用保存的密码登录（登录时按接纳时的凭证版本解密，被替换就停止），步骤 connect / inspect / docker（缺 Docker 才按锁定脚本安装，sudo 用同一个密码经标准输入；需要另外的 sudo 密码或交互式 MFA 时 `sudo_denied` 明确失败；已有的 Docker 沿用、不升级、不全局 prune）/ reconnect（装过 Docker 才重新用密码登录确认 docker 组生效，否则跳过）/ workspace（建平台受控的部署根目录 `~/myworkspace-openclaw`）/ verify（Docker 与 Compose 可用、目录可写、能连到 `AGENT_PUBLIC_URL` 的主机端口；连不上 `ingress_unreachable`）；成功后 `prepared_at` 更新。不装公钥、不建实例、不发接入包，可以重复执行。同一台服务器同时只有一个 SSH 任务（`409 AGENT_SSH_JOB_RUNNING`），与运维动作互斥（`409 AGENT_OPS_RUNNING`） | 已实现（T-0145） |
| POST | `/api/v1/agents/servers/{server_id}/revoke` | 已退役（T-0179，OpenAPI 标 deprecated）：一律 `410 AGENT_SSH_KEY_AUTH_RETIRED`（`details.replacement = legacy-key-cleanup`），不再用旧私钥登录；改用上面的 `legacy-key-cleanup` | 已退役（T-0179；原实现 T-0145） |
| GET | `/api/v1/agents/instances/{instance_id}/deployment` | 实例部署在哪台服务器：`deployment`（`server_id`、`install_dir`、`installed_at`、`runtime_installed`：true 装着 / false 已卸载容器保留目录 / null 旧记录未知，T-0178）、`server`（部署、自动识别或手动指定的那台）、`job`（这个实例最近一次部署或卸载任务，steps、脱敏 log） | 已实现（T-0147） |
| POST | `/api/v1/agents/instances/{instance_id}/deploy` | 部署到服务器（`202`，任务 `kind=install`，`{server_id, version}`；`password` 已弃用、忽略，T-0178）：只替换这个实例自己的目录 `~/myworkspace-openclaw/<实例短号>` 与 Compose 项目 `myworkspace-openclaw-<实例短号>`，同一台服务器上的其他实例不受影响；全程用服务器上保存的登录密码（T-0179）；服务器要可以部署（与服务器列表的 `deploy_block_reason` 同一套规则，与一次点击新建并部署共用 `accept_deployment`，T-0180）：不是 SSH 服务器 `409 AGENT_SERVER_NOT_SSH`、没保存 / 解不开密码 `409 AGENT_SERVER_CREDENTIAL_REQUIRED` / `AGENT_SERVER_CREDENTIAL_UNREADABLE`、主机公钥未确认 `409 AGENT_SSH_HOST_KEY_UNCONFIRMED`、最近一次连接检测主机公钥变化或密码被拒 `409 AGENT_SERVER_CONNECTION_FAILED`、还没初始化好 `409 AGENT_SERVER_NOT_INITIALIZED`（`details.reason`）、同机有任务 `409 AGENT_SSH_JOB_RUNNING`。已经部署在另一台服务器上 `409 AGENT_DEPLOYED_ELSEWHERE`，只支持外网模式 `409 AGENT_SSH_PUBLIC_ONLY`。任务步骤 connect / inspect（环境预检：Docker 不能用就 `docker_unavailable`，不自己装）/ upload / pull / plugin / start / schedule / online / reflection；成功后部署记录 `runtime_installed=true`。同一个实例的重新部署与失败后继续都用这个接口，不新建实例。剩余内存或磁盘不多时 `capacity_warning` 提醒（D4：不拦）。成功后实例的 `server_id` 是这台、`server_manual=true` | 已实现（T-0147） |
| POST | `/api/v1/agents/instances/{instance_id}/undeploy` | 从服务器上卸载（`202`，任务 `kind=uninstall`，`{version, remove_files}`）：用服务器上保存的登录密码登录（T-0179；没保存 / 解不开 `409 AGENT_SERVER_CREDENTIAL_REQUIRED` / `AGENT_SERVER_CREDENTIAL_UNREADABLE`），停止并删除这个实例的容器、删掉它自己标记的定时任务；`remove_files` 时删除它的目录（不可恢复，上一级目录空了一起删），成功后删除部署记录、实例改为未归属；保留目录时部署记录 `installed_at=null`、`runtime_installed=false`（`retained`）。其他实例不受影响 | 已实现（T-0147；密码 T-0179） |
| POST | `/api/v1/agents/deployments` | 一次点击新建智能体并部署（`202`，必须带 `Idempotency-Key`，与 `request_id` 相同，否则 `422`）：`{request_id, server_id, server_version, name, description?, im_nickname?, cpus?, mem_limit_mb?}`（不接受地址、密码、镜像、目录、网络模式、权限开关，多余字段 `422`），自动外网模式、`read` / `member`，资源上限默认不限制；返回 `{request_id, instance, deployment}`。先把幂等记录登记为处理中，再在**一个事务**里登记实例、资源上限、部署记录、部署任务与幂等记录的安全上下文（只有 `instance_id` / `job_id` / `server_id`），提交后才启动任务、注册 OpenIM。前提不满足时什么都不建：服务器 `404` / 版本过期 `409 VERSION_CONFLICT` / 总开关关闭 `409 AGENT_DISABLED` / 外网入口没配置 `409 AGENT_INGRESS_NOT_CONFIGURED` / 不能部署（与部署接口相同的错误码）/ 同机有任务 `409 AGENT_SSH_JOB_RUNNING`，这些记为失败，同一个键重发得到同样的错误。已接纳的请求重发返回同一个实例与它现在的部署状态（`Idempotent-Replayed: true`），不再建、不重启任务；还在处理 `409 IDEMPOTENCY_IN_PROGRESS`；换了内容 `409 IDEMPOTENCY_KEY_REUSED`。同一台服务器不同键并发时只有一个成功（服务器行锁 + 每台服务器一个进行中任务的部分唯一索引），另一个 `409`、不留实例。任务后来失败不影响收据，实例保留，用 `POST /agents/instances/{id}/deploy` 继续 | 已实现（T-0180） |
| GET | `/api/v1/agents/deployments/requests/{request_id}` | 部署请求收据：`{request_id, state: processing \| accepted \| failed \| interrupted, instance, deployment, error_code}`。只能查自己在这个工作空间发出的部署请求（别人的、别的空间的、别的接口用过的键都是 `404`）；从幂等记录的安全上下文找实例与任务，不返回请求原文。`processing` 还在处理（不同于任务 running）；`accepted` 已接纳，进度看 `deployment.job`；`failed` 被拒绝、没建实例（`error_code`）；`interrupted` 处理到一半退出、什么都没建（超过 2 分钟的处理中），可以用同一个 `request_id` 重发。记录保留 24 小时，过期 `404` | 已实现（T-0180） |
| GET | `/api/v1/agents/overview` | 智能体总览（`?server_id&q&status&cursor&limit`，limit 1–100，游标同实例列表）：`summary`（`total` / `online` / `offline` / `pending` / `disabled` / `deploying` 最近一次任务是进行中的部署 / `attention` 最近一次部署失败或中断、或没停用但离线，去重），只按 `server_id` / `q` 统计全部实例，不套用 `status`、不受分页影响；`items`（`instance`、`server` 部署记录优先其次手动 / 自动归属，未归属为 null、`deployment`（含 `runtime_installed`）、`latest_job`、`deployment_project`）、`next_cursor`、`generated_at`。`server_id` 按同样的归属规则筛；`q` 按名称包含（`%` `_` 按字面）。只读本地记录、批量查询：不连 SSH、不调 OpenIM，不输出容器状态（离线 / 停用不等于容器停止） | 已实现（T-0180） |
| PUT | `/api/v1/agents/instances/{instance_id}/server` | 手动指定所在服务器（乐观并发，`{server_id \| null, version}`）：手动部署的实例按上报的服务器指纹自动识别，认错时可以指定（以手动为准，D3）；`null` 改回自动识别。经 SSH 部署的实例以部署为准（`409 AGENT_DEPLOYED`）；审计 `agents.instance.server_assigned` | 已实现（T-0147） |
| GET / POST | `/api/v1/agents/secrets` | 密钥列表 / 新增（只写不读）；列表支持 `q`（名称或说明，字面包含）、`kind` 筛选，先筛选后分页，返回全量匹配数 `total`，游标绑定筛选条件 | 已实现（T-0054；筛选与 total：T-0196） |
| GET | `/api/v1/agents/secrets/{secret_id}` | 单个密钥的元数据（名称、类型、值版本、最近轮换、授权对象；不含值、密文或历史值），只给 owner | 已实现（T-0196） |
| GET | `/api/v1/agents/secrets/accesses` | 取密钥记录（中转站管理连接的 Key 的取用在连接的活动记录里，不在这里，T-0209） | 已实现（T-0054） |
| PATCH / DELETE | `/api/v1/agents/secrets/{secret_id}` | 改名称与说明 / 删除 | 已实现（T-0054） |
| POST | `/api/v1/agents/secrets/{secret_id}/rotate` | 轮换值（旧值不再下发） | 已实现（T-0054） |
| GET | `/api/v1/im/status`、`/api/v1/im/badges` | IM 状态 / 顶栏角标（未读不含免打扰、待审批数） | 已实现（T-0055） |
| GET / POST | `/api/v1/im/conversations` | 会话列表（置顶在前）/ 打开与实例的单聊 | 已实现（T-0055） |
| PATCH | `/api/v1/im/conversations/{conversation_id}` | 置顶 / 免打扰（同步到 OpenIM） | 已实现（T-0055） |
| POST | `/api/v1/im/conversations/{conversation_id}/read` | 已读序号（只增不减） | 已实现（T-0055） |
| GET / POST | `/api/v1/im/conversations/{conversation_id}/messages` | 消息历史 / 发送（必须带 `Idempotency-Key`；结果未知时 `504`，不自动重发） | 已实现（T-0055） |
| GET / POST / DELETE | `/api/v1/im/conversations/{conversation_id}/pins[/{server_msg_id}]` | 消息置顶（每个会话最多 10 条） | 已实现（T-0055） |
| GET | `/api/v1/im/conversations/{conversation_id}/export` | 导出（`application/x-ndjson`） | 已实现（T-0055） |
| POST | `/api/v1/im/uploads` | 上传图片（请求体是图片本身，≤ 10 MiB） | 已实现（T-0055）；T-0119 扩展到文件与视频，见“智能体主动发消息与附件”一节 |
| GET | `/api/v1/im/media/{media_id}` | 显示 OpenIM 对象存储里的图片（外部图片不经这里，前端不自动加载） | 已实现（T-0055）；T-0119 扩展到文件与视频（同上） |
| POST | `/api/v1/im/groups` | 建群（必须带 `Idempotency-Key`） | 已实现（T-0055） |
| GET / PATCH / DELETE | `/api/v1/im/groups/{group_id}` | 群详情 / 改群名与公告 / 解散 | 已实现（T-0055） |
| POST / DELETE | `/api/v1/im/groups/{group_id}/members[/{im_user_id}]` | 邀请 / 移出成员 | 已实现（T-0055） |
| PUT | `/api/v1/im/groups/{group_id}/policy` | 防刷屏设置（允许接力、连续条数、每分钟条数） | 已实现（T-0055） |
| GET | `/api/v1/im/contacts` | 通讯录（没有删除好友） | 已实现（T-0055） |
| GET / POST / PATCH / DELETE / PUT | `/api/v1/im/contact-groups[/{contact_group_id}[/members]]` | 分组 | 已实现（T-0055） |
| GET | `/api/v1/ai/tool-executions[/{execution_id}]` | 工具调用记录与待审批（member 及以下只看自己的）；列表另有 `period`（24h / 7d / all）与 `sort`（created_desc / expires_asc），游标绑定条件；`pending_count` 只统计调用者能看到的记录 | 已实现（T-0057；period / sort：T-0190） |
| GET | `/api/v1/ai/tool-executions/summary` | 审批汇总：有效待审批、3 分钟内到期、高危待审批、执行异常（failed / unknown），`as_of` 与可见范围内的智能体 / 工具筛选项；按 `period`（默认 all）与智能体、风险、工具统计，只读 | 已实现（T-0190） |
| POST | `/api/v1/ai/tool-executions/{execution_id}/approve`、`/reject` | 审批（必须带 `If-Match`；高危操作要重新输入密码） | 已实现（T-0057） |

**实例接口**（只在接入入口；`Authorization: Bearer <实例令牌>`，外网另需客户端证书；失败一律 `401 AGENT_UNAUTHORIZED`）：

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | `/agent-api/v1/secrets/resolve` | 取密钥：未授权与不存在都返回 `NOT_FOUND` | 已实现（T-0054） |
| POST | `/agent-api/v1/im-token` | 取 IM 令牌（只在启动时调用：签发会踢掉旧连接） | 已实现（T-0055） |
| POST | `/agent-api/v1/heartbeat` | 上报 OpenClaw 与插件版本 | 已实现（T-0054） |
| POST | `/agent-api/v1/usage` | 上报 token 用量（接入包的 `bin/usage-report.mjs`，每分钟一次）：`{"day": "YYYY-MM-DD", "sessions": [{"session": "<会话 key 的 SHA-256>", "usage": {"input", "output", "cache_read", "cache_write"} \| null}]}`，最多 500 个会话；只收今天和昨天（UTC），每个实例每分钟最多 6 次；返回 `accepted`、`skipped`、`tokens`、`baseline` | 已实现（T-0104） |
| POST | `/agent-api/v1/host-metrics` | 上报服务器状态（接入包的 `bin/host-report.mjs`，每分钟一次）：`cpu`（`cores`、`model`、`load` 三个数、`/proc/stat` 的累计 `total_ticks` / `idle_ticks`）、`memory`（`total`、`available`、`swap_total`、`swap_free`、`limit` 容器内存上限）、`disks.data` / `disks.root`（`total`、`free`）、`network`（这个实例自己网卡的累计 `rx_bytes` / `tx_bytes`）、`uptime_seconds`；可选的 `container`（自己容器的 cgroup：`cpu_usec` 累计微秒、`mem_bytes`、`pids`，以及每 10 分钟一次的 `data_bytes` / `data_truncated`）与 `host_key`（服务器指纹：`boot_id` 的 HMAC-SHA256，64 位小写十六进制；读不到时这两块缺省，只记整机数据）；只收数字，多余字段 422，数值有上下限；每个实例每分钟最多 2 次（`429`）。返回样本记在哪一分钟（UTC） | 已实现（T-0113、T-0146） |
| POST | `/mcp` | MCP 工具（不进 OpenAPI；工具清单见 `app/schemas/ai_tools.py` 的 `TOOLS`；只接受 POST） | 已实现（T-0057；审批卡片在第 3 批） |
| GET / POST | `/im/ws`、`/im/api/{route}`、`/im/obj/m/{media_id}` | 接入网关（不进 OpenAPI；白名单见 V2 契约第 8 节；取图改为能力地址，见 OC-3 ADR 第 15 节） | 已实现（T-0055）；`/im/obj/m` 在 T-0119 扩展到文件与视频 |
| POST | `/agent-api/v1/wakeups/claim`、`/wakeups/{wakeup_id}/result` | 领取到期的唤醒 / 报回结果（接入包的 `bin/wakeup.mjs`），见“唤醒”一节 | 已实现（T-0121） |
| POST | `/agent-api/v1/stops/claim`、`/stops/{stop_id}/result` | 领取终止令 / 报回结果（接入包的 `bin/stop-runs.mjs`），见“终止正在执行的任务”一节 | 已实现（T-0126） |

- OpenIM 回调 `/internal/openim/{path_token}/{command}` 只走内部网络，不进 OpenAPI（V2 契约第 10 节）。
- 保留的聚合接口与组件：SSE 的 `topics` 增加 `im`、`agents`、`ai`。
- 新增错误码：`409 AGENT_SECRET_EXISTS` / `AGENT_INGRESS_NOT_CONFIGURED`、`401 AGENT_UNAUTHORIZED`、`403 STEP_UP_REQUIRED` / `STEP_UP_FAILED`、`409 AGENT_DISABLED` / `IM_NOT_READY` /
  `IM_SEND_REJECTED` / `PIN_LIMIT_REACHED` / `EXECUTION_NOT_PENDING` / `CONTACT_GROUP_EXISTS`（OC-3 补充）、
  `503 IM_UNAVAILABLE`（V2 契约第 12 节）。

## 知识库（T-0098）

方案见 `docs/v2/KNOWLEDGE_PLAN.md`（人类 2026-11-03 批准），OpenClaw 一侧的核验见 `docs/decisions/2026-11-03-knowledge-k0-verification.md`。
都要 `X-Workspace-Id`；写接口要 CSRF。viewer 可读所有知识库，member 及以上编辑笔记，owner / admin 管理知识库设置、参与的智能体和彻底删除。
笔记的 `version` 是整数，修改、删除、恢复、退回都要带上；不一致时 `409 VERSION_CONFLICT`，`details.current` 是服务器上的最新内容（编辑器据此三方合并）。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/v1/knowledge/spaces[/{space_id}]` | 知识库列表 / 详情：公共库、项目库、智能体库；含篇数、参与的智能体、写入开关、删除是否被暂停 | 已实现（T-0098） |
| PATCH | `/api/v1/knowledge/spaces/{space_id}` | 说明、智能体权限（公共库 `read_write` / `read` / `none`，智能体库 `read` / `none`）、`writes_enabled`、`resume_deletes`（admin） | 已实现（T-0098） |
| PUT / DELETE | `/api/v1/knowledge/spaces/{space_id}/agents/{instance_id}` | 项目库的参与智能体（`read_write` / `read`）（admin） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes` | 笔记列表与搜索：`space_id`、`folder`、`type`、`tag`、`author`（`user` / `agent`）、`q`，游标分页 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes/suggest?q=` | `[[` 补全：按标题、别名找笔记（跨库） | 已实现（T-0098） |
| POST | `/api/v1/knowledge/notes` | 新建（201；同一库标题重复 `409 TITLE_TAKEN`） | 已实现（T-0098） |
| GET / PATCH | `/api/v1/knowledge/notes/{note_id}` | 详情（含出链、反链、被智能体读的次数）/ 修改（可移动到别的库） | 已实现（T-0098） |
| DELETE | `/api/v1/knowledge/notes/{note_id}?version=` | 放进回收站，30 天后 Worker 彻底删除（204） | 已实现（T-0098） |
| POST | `/api/v1/knowledge/notes/{note_id}/restore`、`/revert` | 从回收站恢复 / 退回到某一版（`to_version`） | 已实现（T-0098）；原来的 `/confirm`（人确认可信）随“人已确认”一起删除（T-0102） |
| GET | `/api/v1/knowledge/notes/{note_id}/revisions[/{version}]` | 版本历史 / 某一版与上一版的逐行差异 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/notes/{note_id}/graph?depth=1\|2` | 局部图谱 | 已实现（T-0098） |
| GET | `/api/v1/knowledge/graph` | 全局图谱：笔记、`[[链接]]`、链接到的任务 / 项目 / 书签；`space_id` 可多选，`resources=false` 只要笔记；`files=true` 加上图片节点（`kind = file`，连到正文里引用它的笔记）；超过 2000 篇时 `truncated` | 已实现（T-0098；`files` 为 T-0103） |
| POST | `/api/v1/knowledge/files` | 上传图片（member 以上）：请求体就是图片本身；只收 PNG / JPEG / GIF / WebP（按文件头判断，SVG 不收），单张不超过 `FILE_MAX_MB`、工作空间合计不超过 `FILE_QUOTA_MB`（413 `PAYLOAD_TOO_LARGE` / `QUOTA_EXCEEDED`，422 `UNSUPPORTED_IMAGE`）；JPEG 去掉 Exif 等元数据（保留方向）；同一工作空间同样的内容返回已有的那张。返回 `FileOut`（`url` 写进正文 `![说明](url)`）；存储没配置完整或不可用时 503 `STORAGE_NOT_CONFIGURED` / `STORAGE_UNAVAILABLE` | 已实现（T-0103） |
| GET | `/api/v1/knowledge/files/{file_id}` | 读取图片：**不需要 `X-Workspace-Id`**，按图片所属的工作空间校验当前用户（viewer 以上），不是成员与不存在一样 404（笔记里的 `<img>` 由浏览器直接加载）。`Cache-Control: private`、`nosniff`、`ETag`（内容的 SHA-256，支持 `If-None-Match` → 304）；先读写入时的存储，失败再读主存储与副本，内容哈希对不上的跳过 | 已实现（T-0103） |
| GET | `/api/v1/knowledge/overview?updated_after=&updated_before=[&space_id=][&recent_limit=]` | 知识总览（viewer）：`summary`（`note_count` 未删除笔记数、`space_count` 知识库数（含已归档、没有笔记的；选了知识库为 1）、`link_count` 两端都是未删除笔记的 `[[链接]]` 按有向 (源, 目标) 去重（不含自己链自己，选了知识库时至少一端在库里，任务 / 项目 / 书签 / 图片不算）、`updated_note_count` `updated_at` 落在 `[updated_after, updated_before)` 的不同笔记数）、`recent_notes`（`NoteSummary`，按 `updated_at`、`id` 倒序，不含正文，默认 6 篇、最多 20）、`generated_at`。全量统计，不受图谱 2000 篇上限与分页影响；两个时间必填、带时区且前者早于后者（否则 422），只决定 `updated_note_count`；别的工作空间的知识库 404。只读：不写版本、审计、智能体阅读用量 | 已实现（T-0172） |
| GET | `/api/v1/knowledge/changes` | 变更记录：每次改动（谁、何时、增删行数、理由、能否一键退回）与保护记录（`kind=guard`） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/trash` | 回收站 | 已实现（T-0098） |
| DELETE | `/api/v1/knowledge/trash/{note_id}` | 彻底删除（admin，204） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/agents/{instance_id}` | 智能体主页：自己的库、参与的项目、近 30 天写 / 读 / 被纠正 / 被拦下的次数；`weekly` 是近 8 周的趋势（T-0100） | 已实现（T-0098） |
| GET | `/api/v1/knowledge/spaces/{space_id}/activity` | 近 8 周人与智能体的修改次数（新建、修改、改名、退回），按周一起算（UTC），最早的在前 | 已实现（T-0100） |

**MCP 工具**（`/mcp`，工具清单见 `app/schemas/ai_tools.py`）：`knowledge_search`、`knowledge_get`、`knowledge_list`、`knowledge_brief`（风险级别 `read`），
`knowledge_write`、`knowledge_delete`（风险级别 `knowledge`：**直接执行、不进审批**，人类 2026-11-03 决定；仍要通过实例的读写范围与角色检查）。

- 智能体能读写哪些库：公共库按 `agent_access`；项目库只有参与的智能体；自己的库读写，别人的库按对方的 `agent_access`。项目归档或项目 / 实例已删除的库只读。
- 智能体写入：正文不超过 20 KB（人 256 KB），每个库最多 5000 篇；必须带 `idempotency_key`（同一个键、不同参数 `409 IDEMPOTENCY_KEY_REUSED`）、`reason`，可带 `evidence`；
  不能写项目简报（`BRIEF_HUMAN_ONLY`）和《收到的纠正》（`SYSTEM_NOTE`），不能移动笔记（`MOVE_NOT_ALLOWED`）；与已有笔记同名或标题相近时写入照常，两篇都标上“有冲突”（人编辑后清除），`notices` 里提醒智能体去合并。
- 保护：内容里有密钥 `422 SECRET_DETECTED`（不保存内容，只记规则名）；每个实例每小时最多写 20 次（`429 RATE_LIMITED`）、删 5 次，超过后这个库的删除自动暂停（`403 DELETES_PAUSED`）并通知管理员（`knowledge_delete_paused`）；管理员关了写入时 `403 WRITES_DISABLED`。
- 读到的笔记内容放在 `untrusted` 里；`notices` 提示新的纠正和简报更新。智能体的通用 `search` 工具不搜笔记。
- 全局搜索增加 `type=note`；SSE 增加主题 `knowledge`。
- 新增错误码：`409 TITLE_TAKEN` / `NOTE_DELETED` / `NOT_IN_TRASH`、`422 NOTE_TOO_LARGE` / `SECRET_DETECTED` / `SPACE_FULL` / `MOVE_NOT_ALLOWED` / `SYSTEM_NOTE` / `REVISION_NOT_FOUND`、
  `403 READ_ONLY` / `WRITES_DISABLED` / `DELETES_PAUSED` / `BRIEF_HUMAN_ONLY`。

### 认知对齐与自我提升（T-0100）

方案 3.9、3.10。
- **项目简报已读**：`knowledge_brief` 记下这个实例读到的版本，知识库主页显示谁没读最新版。
  - knowledge_* 工具的 `notices` 在参与的项目有未读简报时提醒先读（最多 3 条）。提醒只带 `project_id` 和版本号，是工作台写的固定文字，不含人填的项目名。
  - MCP `get_task` 的输出增加 `brief`：任务所属项目的简报版本 `version`、这个实例读到的 `read_version`、`unread`；项目没有简报或实例不参与时为 `null`。
- **“现在发给它”**：管理员在项目知识库主页点按钮，前端用已有的 IM 接口，以管理员自己的账号把“去读最新简报”发给那个智能体。
  - 先 `POST /api/v1/im/conversations {instance_id}`，再 `POST /api/v1/im/conversations/{id}/messages`，带 `Idempotency-Key`；同一个智能体、同一版简报重试用同一个键。
  - 没有新接口：接入包的渠道只放行管理员的消息（K-0 核验 3.4），工作台不替管理员自动发。
- **每晚复盘**：
  - 接入包增加 `reflection-prompt.md`（提示词）和 `setup-reflection.sh`，后者用 `openclaw automations add --declaration-key myworkspace-knowledge-reflection` 建 OpenClaw 定时任务：独立会话、不投递，重复运行只更新。
  - SSH 部署的步骤增加 `reflection`，在 `online` 之后执行；建不成记为 `skipped`，不算安装失败，CHECKLIST 给出手工命令。
  - 时间由 `AGENT_REFLECTION_CRON`（默认 `0 22 * * *`）和 `AGENT_REFLECTION_TIMEZONE`（默认 `Asia/Shanghai`）设置，生成接入包时写进脚本。
- **趋势**：`GET /api/v1/knowledge/spaces/{space_id}/activity`（上表）、`AgentKnowledgeOut.weekly`：
  - 每周读自己知识库的次数 `reads_own`；
  - 读项目、公共和别的智能体知识库的次数 `reads_shared`；
  - 写入次数 `writes`。

## Gmail 邮件（T-0128 ~ T-0130）

方案 `docs/v2/MAIL_PLAN.md`，第三方接口核验 `docs/decisions/2026-11-06-gmail-verification.md`。人这边只有 owner / admin 能用（member、viewer 一律 `403`）；
邮件内容实时从 Gmail 读，不落库、不缓存，响应都带 `Cache-Control: no-store`。发件人、主题、正文、附件名是外部输入，页面按纯文字显示。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/mail/settings` | OAuth 客户端：`configured`、`client_id`、`client_secret_set`（不返回密钥）、`redirect_uri`（要登记到 Google 的回调地址）、`version` |
| `PUT /api/v1/mail/settings` | 保存 `client_id`、`client_secret`（第一次必填，之后不填表示不变）、`version`（乐观并发 `409 VERSION_CONFLICT`）；审计 `mail.client_updated` |
| `POST /api/v1/mail/connect` | 返回 Google 授权地址 `authorize_url`（`access_type=offline`、`prompt=select_account consent`（每次选账号，方便连接多个邮箱，T-0132）、PKCE S256、`state` 10 分钟一次性、绑定发起的用户与工作空间）；可带 `login_hint`。没设置客户端 `409 MAIL_NOT_CONFIGURED` |
| `GET /api/v1/mail/oauth/callback` | 浏览器从 Google 跳回（不带工作空间请求头，要登录会话）：校验 `state` 属于当前用户、再查 admin 角色，换令牌、读邮箱地址，按实际授权的范围记下能读 / 能发；`303` 跳到 `/settings/mail?mail=connected` 或 `?mail=<错误码>`（`MAIL_STATE_INVALID`、`MAIL_ACCESS_DENIED`、`MAIL_OAUTH_FAILED`、`MAIL_SCOPE_MISSING`、`MAIL_NO_REFRESH_TOKEN`、`MAIL_CLIENT_INVALID`、`FORBIDDEN`）；审计 `mail.connected` |
| `GET /api/v1/mail/accounts` | 已连接的邮箱（可以有多个）：`status`（`active` / `needs_reconnect`）、`can_read`、`can_send`、`agents`（分配了这个邮箱的实例 `{id, name}`，T-0132）、`agent_signature`、`last_error`、`version` |
| `PATCH /api/v1/mail/accounts/{id}` | 改 `agent_signature`（智能体发信加署名行），带 `version`；审计 `mail.account_updated`。原来的 `agents_allowed` 已去掉（T-0132，传了返回 422） |
| `PUT /api/v1/mail/accounts/{id}/agents` | 把邮箱分配给智能体（T-0132）：`instance_ids`（整组替换，≤ 200）、`version`（邮箱的；`409 VERSION_CONFLICT`）。不是本工作空间的实例 `422`；变化的实例 `version` 也加 1；审计 `mail.account_agents_updated`（记新增、取消的实例 ID） |
| `GET /api/v1/mail/agents` | 可以分配邮箱的实例，只有 `id`、`name`（admin 用；实例列表本身只有 owner 能看） |
| `DELETE /api/v1/mail/accounts/{id}` | 断开：尽力撤销 Google 授权、删除保存的令牌、取消它对智能体的全部分配，发件记录保留；审计 `mail.disconnected`（带取消的分配数） |
| `GET /api/v1/mail/accounts/{id}/labels` | 标签（`unread` 可能为空） |
| `GET /api/v1/mail/messages`（T-0170） | 邮件页默认的“全部邮箱”：不填 `account_id` 读全部已连接的邮箱，按 Gmail `internalDate` 倒序合并成一批（不跨邮箱去重）；填了只读这个邮箱。`q`、`label`（全部邮箱时只接受系统标签，自定义标签 `422 MAIL_LABEL_NEEDS_ACCOUNT`）、`cursor`、`per_account_limit`（1–20，默认 10）。返回 `items`（每封带 `account_id`、`account_email`、`account_display_name`）、`accounts`（每个邮箱的 `status`：ok / needs_reconnect / rate_limited / unavailable / pending，`unread` 没有依据时为 null）、`outcome`、`partial`、`next_cursor`（15 分钟有效；范围、查询、用户或工作空间变了 `422 MAIL_CURSOR_MISMATCH`，过期 `422 MAIL_CURSOR_EXPIRED`）、`total_estimate`（部分失败时为 null）。一个邮箱失败不影响其他邮箱；`Cache-Control: no-store`。读信、会话、附件、回复仍用下面带 `{id}` 的接口 |
| `GET /api/v1/mail/accounts/{id}/messages` | 查收 / 搜索：`q`（Gmail 搜索语法）、`label`（标签 ID）、`page_token`、`limit`（≤ 50）；只取 metadata |
| `GET /api/v1/mail/accounts/{id}/messages/{message_id}` | 一封邮件：纯文字正文（只有 HTML 时后端转成纯文字，去掉脚本、样式、图片，链接留地址）、超过 20000 字截断（`truncated`）、附件清单 |
| `GET /api/v1/mail/accounts/{id}/threads/{thread_id}` | 会话，按时间顺序最多最近 20 封 |
| `GET /api/v1/mail/accounts/{id}/messages/{message_id}/attachments/{attachment_id}` | 下载附件：`application/octet-stream`、`Content-Disposition: attachment`、`X-Content-Type-Options: nosniff`；最大 25 MB（`413 PAYLOAD_TOO_LARGE`） |
| `POST /api/v1/mail/accounts/{id}/send` | 发纯文字邮件：`to`（1 ~ 20）、`cc`、`bcc`（合计 ≤ 20）、`subject`（≤ 200 字，回复时可不填）、`body`（≤ 20000 字）、`reply_to_message_id`（带 `threadId`、`In-Reply-To`、`References`，主题加 “Re: ”）。内容像密钥 `422 SECRET_BLOCKED`；Gmail 超时 `504 TIMEOUT_UNKNOWN`（记为 `unknown`，不自动重发）；审计 `mail.sent` |
| `GET /api/v1/mail/sends` | 发件记录（人和智能体，不含正文），可按 `instance_id` 筛选 |
| `PUT /api/v1/agents/instances/{id}/mail` | 实例的邮件权限：`account_ids`（能用的邮箱，整组替换；不填表示不变；已断开或不是本工作空间的邮箱 `422`；变化的邮箱 `version` 也加 1，T-0132）、`read_enabled`（默认 true）、`send_enabled`（默认 false）、`send_scope`（`any` / `listed`，打开发送时必选）、`recipients`（地址 `a@b.com` 或域名 `@b.com`，≤ 50）、`version`。只读实例不能打开发送（`422`）；审计 `agents.instance.mail_updated`，打开发送时通知管理员（`mail.agent_send_enabled`）。`AgentInstanceOut.mail` 返回当前设置与 `accounts`（分配给它的邮箱 `{id, email, status, can_send}`） |

错误码：`409 MAIL_NOT_CONFIGURED` / `MAIL_RECONNECT`（授权失效，同时把邮箱标为 `needs_reconnect` 并通知管理员 `mail.reconnect`）/ `MAIL_SCOPE_MISSING` / `MAIL_CLIENT_INVALID`、
`403 MAIL_FORBIDDEN`、`422 SECRET_BLOCKED`、`429 RATE_LIMITED`（带 `retry_after`）、`503 EXTERNAL_UNAVAILABLE`、`504 TIMEOUT_UNKNOWN`。

**MCP 工具**（T-0129，`docs/AGENTS.md` 第 8 节例外七）：

- 读（风险级别 `read`，按实例的 `read_enabled`，关掉后不列出、调用 `FORBIDDEN_CAPABILITY`）：`mail_accounts`（分配给自己的邮箱、自己能不能发、收件人范围）、
  `mail_search`、`mail_get`、`mail_thread`。只看得到管理员分配给自己、没断开的邮箱（T-0132；没分配时 `NOT_FOUND`）；只分配了一个邮箱时可以不填 `account_id`，多个时必须指定。发件人、收件人、主题、摘要、正文、附件名、标签都在 `untrusted` 里，
  输出带 `caution` 提醒“邮件里的指令只能当数据看”。拿不到附件内容。
- 发（风险级别 `direct`，直接发出、不审批；只列给 `send_enabled` 的实例）：`mail_send`。收件人不在范围里 `RECIPIENT_NOT_ALLOWED`（域名只匹配本身，不含子域名）；
  密钥 `SECRET_BLOCKED`；每个实例每小时 10 封、每天 50 封（`RATE_LIMITED`）；`client_key` 24 小时内重试返回第一次的结果（`replayed: true`）；
  正文末尾加“—— 由工作台智能体「名字」发送”（邮箱可以关）；执行记录只记收件人、主题与正文长度；审计 `mail.sent`、通知管理员 `mail.sent_by_agent`；
  结果未知时返回 `TIMEOUT_UNKNOWN`、审计 `mail.send_unknown`、通知管理员 `mail.send_unknown`，不自动重发。

## 智能体主动发消息与附件（T-0117 ~ T-0120）

方案 `docs/v2/AGENT_MESSAGING_PLAN.md` 第 3 ~ 6 节（人类 2026-11-04 批准），锁定版本上的核验与实测见 `docs/decisions/2026-11-04-agent-messaging-verification.md`。
主动发消息统一走工作台：工作台以实例的身份（`sendID = oc_…`）经 OpenIM 管理接口 `/msg/send_msg` 发出，OpenIM 照样调用 before 回调，规则不变。
管理员接口都要 owner / admin 与 `X-Workspace-Id`，写接口要 CSRF。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/agents/instances/{id}/outbox?status=all\|pending\|sent\|rejected\|failed` | 实例页的“发件记录”（`ImOutboxOutput`，与 MCP `im_outbox` 相同）：`im_send` 发的消息（最近 50 条：目标、类型、状态、来源 `tool` / `reply_retry` / `wakeup`、拒收码与中文原因、文字前 200 字）；最近 7 天被回调拒收的自动回复 `rejected_replies`（`retried` 表示工作台已补发）；还没执行的唤醒 `wakeups` |
| `POST /api/v1/agents/instances/{id}/outbox/cancel` | `{message_ids}`（1 ~ 20 个）取消排队中、定时的消息；已发出、已结束或不属于这个实例的放进 `not_cancellable` |
| `PUT /api/v1/agents/instances/{id}/im-media` | 实例的附件权限 `{enabled, version}`（乐观并发，`409 VERSION_CONFLICT`），**默认关闭**：打开后它能经 `im_send` 发图片、文件、视频，文字不受影响；审计 `agents.instance.im_media_enabled` / `agents.instance.im_media_disabled`。`AgentInstanceOut.im_media` 返回 `enabled`、`granted_by`、`granted_at` |
| `POST /api/v1/im/uploads?name=` | 管理员上传（T-0119 起含文件与视频）：请求体是文件本身；图片 PNG / JPEG / GIF / WebP 10 MiB，文件 20 MiB，视频 MP4 / MOV 50 MiB，超过 `413 PAYLOAD_TOO_LARGE`；按内容核对类型，不只看 Content-Type，可执行与网页类文件不收。`ImUploadOut` 增加 `kind`、`name`、`duration_seconds`。发送时 `ImMessageSend.kind` 可以是 `file` / `video`，与图片一样只填 `upload_id`。Caddy 只给这个路径放宽到 51 MB |
| `GET /api/v1/im/media/{media_id}` | 图片内联显示；文件一律 `Content-Disposition: attachment`；视频支持 `Range`（`206` / `416`，`Accept-Ranges: bytes`）；都带 `X-Content-Type-Options: nosniff` |
| `GET /im/obj/m/{media_id}`（接入入口，不进 OpenAPI） | 实例取已发出的图片、文件、视频，响应头规则同上 |

- `AgentInstanceOut.bundle_outdated`（T-0120）：实例上报的插件版本不是 `0.3.3+myworkspace.2`（插件没打工作台的补丁，或只有 T-0120 的旧补丁；T-0221）时为 true，实例页提示重新签发接入包或经 SSH 重新部署。
- 签发接入包时记下当时的私聊白名单（`agent_instances.bundle_whitelist`，迁移 0033），`im_list_targets` 据此给出 `replies_reach_you`。

**MCP 工具**（`/mcp`，工具清单见 `app/schemas/ai_tools.py`）：

- 读（风险级别 `read`）：
  - `im_list_targets`（`kind` = `all` / `direct` / `group`，`query` 按名字筛）：`me`（自己的 IM 用户 ID、`im_ready`）；`direct` 是能私聊的人，即本工作空间状态正常的 owner / admin（`target` = `user:<IM 用户 ID>`、角色、单聊会话 ID、最后一条消息的时间、`replies_reach_you`：不在接入包白名单里为 false 并附提示，接入包签发早于迁移 0033 时为 null）；
    `groups` 是自己当前所在的群（`target` = `group:<群 ID>`、关联的项目、成员与 IM 用户 ID、`rules` 是否允许接力 / 每分钟上限 / 连续上限、`state` 是否暂停 / 当前连续条数 / 这一分钟剩余条数）；
    `recent_rejections`（最近 24 小时被拒收的条数）、`sends_left_this_hour`、`attachments_enabled`、`attachments_left_this_hour`、`notes`。不列其他实例的私聊（实例之间不能私聊）。
  - `im_read_messages`（`target`、`limit` 默认 20 最多 50、`before` 消息 ID 翻页）：只读自己的会话（与管理员的私聊、所在的群里入群之后的消息），数据来自工作台的消息投影 `im_messages`，不新开 OpenIM 读接口；
    单条文字超过 2000 字截断（`truncated`）；附件给出 `ref = im_media:<ID>`，可以在 `im_send` 里转发。
  - `im_outbox`（`status` = `all` / `pending` / `sent` / `rejected` / `failed`、`since`、`limit` ≤ 50）：同上面的发件记录。
- 直接执行（风险级别 `direct`，只列给 `scope = read_write` 的实例；`docs/AGENTS.md` 第 8 节例外四）：
  - `im_send`：`target`；`text`（≤ 4000 字）；`mention`（≤ 20 个 IM 用户 ID，只能在群里、要和文字一起发，必须是群成员）；`attachments`（≤ 4 个，见下）；
    `send_at`（定时，最远 30 天；1 分钟以内的按立即发）；`client_key`（8 ~ 64 位；不给时按目标 + 内容 + 10 分钟时间窗生成。同一个键、内容相同返回第一次的结果 `replayed: true`，内容不同 `IDEMPOTENCY_MISMATCH`）；
    `if_rate_limited`（`queue` 默认：遇到 5002 排到限额恢复后发，最多等 10 分钟；`fail` 直接返回拒收）。先发文字、再按顺序发附件，每个算一条（`part`）。
    返回整体 `status`（`sent` / `queued` / `scheduled` / `rejected` / `failed` / `partial`）与每一条的 `message_id`、状态、拒收码、原因、`retryable`、`retry_after_seconds`。
  - `im_cancel`（`message_ids` 1 ~ 20 个）：取消自己排队中、定时的消息。
- 附件（T-0119；实例的附件权限没打开时 `ATTACHMENT_FORBIDDEN`）：每个只给一种来源——`url`（公网网址，由工作台去取）、`data_base64`（≤ 512 KB）、`text_content` + `filename`（≤ 1 MB，扩展名要是 `.md` / `.txt` / `.csv` / `.json` 等文字类）、`ref = im_media:<ID>`（本工作空间、它自己会话里收到或发出过的媒体）。
  `kind` = `auto` / `image` / `file` / `video`；视频可带 `cover`（只能是图片，不给时用工作台的默认封面图）。按文件头识别：图片 10 MB、文件 20 MB、视频 MP4 / MOV 50 MB（读不出时长的视频按文件发）；
  每个实例每小时 30 个附件、每 24 小时合计 200 MB（`RATE_LIMITED`）。不支持 OpenClaw 服务器上的本地文件；一批附件里有一个不能发，整批都不发。执行记录只记附件的来源、文件名与长度，不记内容。真实 OpenIM 上发文件、视频的实测（核验记录 V1、V5、V6）随 T-0125 验收。
- 投递：发送前按回调的同一套规则只读预检（5001 / 5002 / 5004 / 5006，不计数），明显会被拒收的当场返回原因；工具调用里最多等 12 秒，没发完返回 `queued`，Worker 每 5 秒接着发，同一个会话按顺序发。
  5002 按 `if_rate_limited` 排队；5009、连不上或确定没有发出时退避重试（5 秒、30 秒、2 分钟、10 分钟、30 分钟，最多 5 次、总共不超过 1 小时），仍不行记为 `failed`；
  请求发出但没拿到结果记为 `unknown`，先按 `ex` 里的发件箱 ID 在消息投影里对账，5 分钟后仍找不到才重发。实例停用或总开关关闭时未发出的全部取消。
- 限额：每个实例每小时最多主动发 60 条（`RATE_LIMITED`）；群里原有的每分钟 6 条、连续 8 条暂停不变。
- 回调（T-0118、T-0119）：实例发的消息被拒收时（主要是插件发的自动回复）记一条拒收记录（拒收码、消息类型、脱敏后的前 120 字）；
  5002 拒收的群文字回复由工作台在限额恢复后以实例的身份补发（最多等 2 分钟，每群每实例最多排 3 条，`source = reply_retry`）。
  新拒收码 `5007`（智能体只能经工作台发附件）：实例发非文字消息时，`ex` 里必须带工作台的签名，而且对应一条这个实例正在发送的发件记录。
- 接入包（T-0120）：`tools.deny` 加上 `message`、`conversations_send`、`conversations_turn`、`conversations_list`，主动发消息只剩 `im_send`；
  openclaw-channel 打补丁后，自动回复被拒收时把“刚才的回复没发出（拒收码）：原因。怎么办”作为系统事件放回同一个会话，智能体下一轮看得到（核验记录 6.1）。
- 错误码：`TARGET_NOT_ALLOWED`（目标不是能私聊的管理员或自己所在的群）、`IDEMPOTENCY_MISMATCH`、`IM_NOT_READY`、`AGENT_DISABLED`、`RATE_LIMITED`、`EXTERNAL_UNAVAILABLE`、
  `ATTACHMENT_FORBIDDEN` / `ATTACHMENT_FETCH_FAILED` / `ATTACHMENT_TOO_LARGE` / `ATTACHMENT_TYPE_NOT_ALLOWED` / `ATTACHMENT_SECRET_BLOCKED` / `ATTACHMENT_NOT_FOUND`、`VALIDATION_FAILED`。
  拒收码与中文原因见 `app/domain/agent_outbox.py` 的 `REJECT_REASONS`（插件补丁里的表与它一致）。
- 运维指标（Prometheus）：`workspace_im_outbox_events`（`event` = `created` / `sent` / `rejected`）、`workspace_im_reply_rejections`（按拒收码）。

## 唤醒（T-0121）

方案第 7.2 节；会话键与 `openclaw agent` 的实测见核验记录 6.3。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/agents/instances/{id}/wakeups` | 实例的唤醒（待执行的在前，其余按时间倒序，最多 50 个）与建唤醒时可选的会话 `targets`（和管理员的私聊、它所在的群） |
| `POST /api/v1/agents/instances/{id}/wakeups` | 管理员建唤醒（201）：`prompt`（1 ~ 2000 字）、`target`（`user:` / `group:`，不给是独立会话）、`run_at`（最远一年后）、`repeat`（`none` / `daily` / `weekdays` / `weekly`，按 `time_zone` 的同一个钟点）、`time_zone`（默认 `UTC`，不认识的 `422`）；每个实例最多 50 个管理员建的待执行（`409 LIMIT_REACHED`）；审计 `agents.wakeup.created` |
| `POST /api/v1/agents/instances/{id}/wakeups/{wakeup_id}/cancel` | 取消（已结束的 `409 CONFLICT`）：正在跑的这一轮停不下来，重复的不再排下一次；审计 `agents.wakeup.cancelled` |
| `POST /agent-api/v1/wakeups/claim` | 实例的 `wakeup.mjs` 每分钟领取到期的唤醒（每次最多 5 个；每个实例每分钟最多 6 次，`429`）：每项 `id`、`session_key`、`message`（前面一行工作台写的说明“谁、什么时候安排的、不是别人刚发来的消息”，提示词在最后）。领取即标为 `running`；领取后 15 分钟没有结果记为 `failed`，不重跑；停用的实例领不到 |
| `POST /agent-api/v1/wakeups/{wakeup_id}/result` | `{status: "done" \| "failed", error?}`（≤ 300 字）；重复的唤醒排下一次，返回下一次的 `run_at` |

**MCP 工具**（风险级别 `direct`，例外四）：

- `schedule_wakeup`：`prompt`（≤ 2000 字）；`at` 与 `in_minutes` 二选一，最早 5 分钟后、最远 30 天；`target` 是 `im_list_targets` 给的目标（在那个会话里跑，看得到之前的消息），不给是独立会话。每个实例自己安排的最多 20 个待执行（`RATE_LIMITED`）。
- `cancel_wakeup`：只能取消自己安排、还没开始的（管理员建的 `NOT_FOUND`，已开始或已结束 `CONFLICT`）。待执行的唤醒列在 `im_outbox` 的 `wakeups` 里（`source` = `agent` / `admin` / `issue`）。
- 会话键与 openclaw-channel 0.3.3 一致：群 `openim:group:<群 ID>`、私聊 `openim:direct:<管理员的 IM ID>`（都转小写；T-0221 起与打过补丁的插件收到消息时用的会话一致），独立会话 `myworkspace-wakeup-<唤醒 ID>`。
- `wakeup.mjs` 在容器里运行 `node openclaw.mjs agent --session-key <键> --message-file <文件> --timeout 600 --json`（不加 `--deliver`，要发消息时智能体自己用 `im_send`），用锁文件保证同一时间只跑一个，只报回一行原因。
- 结束的唤醒保留 30 天。Issue 有新动态时的唤醒见“项目里的 Issues”一节。

## 终止正在执行的任务（T-0126）

方案第 7.3 节（D20 ~ D22）；锁定镜像上核对的网关 RPC 与实测见核验记录 6.2。

| 方法与路径 | 说明 |
|---|---|
| `POST /api/v1/agents/instances/{id}/stop` | 管理员叫停（201）：`reason`（1 ~ 500 字）、`task_id`（可选：它正在执行的任务，这一段记为 `interrupted`，任务仍归原执行者；不是它在执行的 `422`）；已停用的实例 `409 CONFLICT`；立即打开软停止；审计 `agents.run.stop_requested`。实例页和任务抽屉、时间轴的“终止执行”都调这个接口 |
| `GET /api/v1/agents/instances/{id}/stops` | 最近 20 条终止记录：`status`（`pending` / `running` / `done` / `failed` / `expired`）、`soft_until`、`aborted`（中止的会话数）、`requested_by` / `requested_by_instance`、`task_id`、`error` |
| `PUT /api/v1/agents/instances/{id}/stop-others` | “终止其他智能体任务”的权限 `{enabled, version}`（乐观并发），**默认关闭**；审计 `agents.instance.stop_others_enabled` / `agents.instance.stop_others_disabled`。`AgentInstanceOut.stop_others` |
| `POST /agent-api/v1/stops/claim` | 实例的 `stop-runs.mjs` 每分钟领取（每个实例每分钟最多 6 次）：返回终止令的 `id`，领取即 `running`；10 分钟没被领取记为 `expired`（实例不在线或接入包太旧），领取后 5 分钟没有结果记为 `failed` |
| `POST /agent-api/v1/stops/{stop_id}/result` | `{status: "done" \| "failed", aborted（0 ~ 1000）, error?}`；`done` 而且这个实例没有别的未结束的终止令时解除软停止 |

- **软停止**：下令后 5 分钟内，这个实例调用工作台的任何 MCP 工具都返回 `RUN_STOPPED`（谁、因为什么叫停，要它停下、不再调用工具、交代一句进展后结束这一轮）；这个检查排在每分钟限流之前。
- **硬停止**：`stop-runs.mjs` 在容器里调本机网关 `sessions.list({activeOnly: true})`，对每个会话调 `sessions.abort({key, clearQueued: true})`（`global` / `unknown` 行改带 `agentId`、不带 `clearQueued`），报回中止了几个；没有终止令时不调网关。没有更新接入包的实例只有软停止。
- **MCP 工具**（风险级别 `direct`，例外六；只列给打开了这个权限的实例，没打开时调用返回 `FORBIDDEN_CAPABILITY` 并记为拒绝）：
  - `list_agent_runs`：本工作空间实例的状态、最近在线时间、正在执行的任务（时间轴上没结束的一段）、是否已被叫停、`is_you`；名字与说明在 `untrusted` 里。
  - `stop_agent_run`：`instance_id`、可选的 `task_id`、`reason`（≤ 500 字）；返回 `stopping`、`stop_id`、`soft_until`。不能叫停自己（`VALIDATION_FAILED`），只能叫停本工作空间里的实例（否则 `NOT_FOUND`）；
    每个实例每小时 10 次（`RATE_LIMITED`）；审计 `agents.run.stop_requested`，通知管理员（`agent.run_stopped_by_agent`）。
- 已结束、下令超过 30 天的终止令由 Worker 删除。

## 项目与任务的 README（T-0122）

方案第 9 节（D16 ~ D18）。README 是项目知识库里类型为 `readme` 的笔记：项目 README 是根目录里标题为 `README` 的一篇（置顶，每个项目知识库最多一篇）；
任务 README 在同一个知识库的“任务导览”文件夹，标题“<任务标题>（任务 <ID 前 8 位>）”，笔记的 `task_id` 指向任务（每个任务最多一篇）。项目、任务创建时按模板生成（不调用模型），迁移前已有的由 Worker 补建。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/projects/{project_id}/readme` | 项目 README（viewer）：`ReadmeOut` = `note`（`NoteOut`）、`project_id`、`task_id`（null）、`auto_sections`（标记还在、由工作台自动更新的部分）；还没有就当场生成 |
| `GET /api/v1/tasks/{task_id}/readme` | 任务 README（viewer），同上 |
| `PATCH /api/v1/knowledge/notes/{note_id}` | 编辑 README 照常用知识库的笔记接口（版本号、密钥拦截）；README 不能新建、改类型、换知识库或文件夹、删除（`422 SYSTEM_NOTE`） |
| `GET /api/v1/knowledge/graph?task_readmes=true` | 全局图谱默认不显示任务 README，带这个参数才显示 |

- 自动部分用 `<!-- myws:auto 名字 -->` … `<!-- /myws:auto -->` 包起来：项目 README 的“成员”“沟通”“任务概况”“知识库”，任务 README 的“状态”“讨论”“执行记录”。
  相关数据变了由 Worker 合并后更新（每 20 秒取一次，同一篇 1 分钟内最多更新一次；每 30 分钟对账补建），只替换标记之间的内容，内容真的变了才写一个版本（作者是工作台）；删掉标记的部分不再自动更新。
- 任务移到别的项目时 README 跟着移到新项目的知识库；任务删除时 README 进回收站。

**MCP 工具**（只能用在自己参与的项目上，别的一律 `NOT_FOUND`）：

- `readme_get`（风险级别 `read`）：`project_id` 或 `task_id` 二选一；返回 `readme`（`note_id`、`version`、`auto_sections`，标题与正文在 `untrusted` 里）和全部的部分标题 `sections`。
- `readme_update`（风险级别 `direct`，例外五）：`section`（`## ` 后面的标题，1 ~ 60 字）、`text`（≤ 20000 字，不能带二级标题和自动部分的标记）、`mode`（`append` 默认 / `replace`）；没有这一部分就加在最后；
  自动部分改不了（`AUTO_SECTION`）；内容没变返回 `unchanged`。经知识库写入，读写权限、写入开关、密钥拦截、每小时写入限额照旧；执行记录里正文只记长度。
- `list_projects` 的每项、`get_task` 带 `readme`（`note_id`、`version`）；`claim_task` 成功时带任务 README 全文；`release_task` / `complete_task` 的 `next_step` 提醒更新“进展与交接”。

## 项目里的 Issues（T-0123、T-0124）

方案第 8 节（D11 ~ D15、D19）。Issue 属于项目，按项目编号（`#1`、`#2`……），任务经“关联”看到自己的 Issue。都要 `X-Workspace-Id`，写接口要 CSRF。
viewer 能看；member 能开、评论、改标签、指派、关联任务，关闭 / 重新打开自己开的或指派给自己的；owner / admin 还能关闭任何 Issue、锁定、隐藏评论、删除。归档的项目只读（`409 PROJECT_ARCHIVED`），删除项目时 Issue 一起删除。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/issue-templates` | 八种类型（`question` 提问 / `proposal` 提议 / `bug` 缺陷 / `contract` 接口约定 / `handoff` 交接 / `review` 评审请求 / `blocker` 阻塞 / `other` 其他）与各自的正文模板 |
| `GET /api/v1/projects/{project_id}/issues` | 列表（viewer），按最近动态排列：`state`、`kind`、`label`、`assigned_to_me`、`mentioned_me`、`q`；每项带 `unread`、`agent_paused` |
| `POST /api/v1/projects/{project_id}/issues` | 开 Issue（member）：`title`（≤ 200 字）、`body`（Markdown，≤ 20000 字）、`kind`、`labels`（≤ 10 个）、`assignee_user_ids` / `assignee_instance_ids`（合计 ≤ 20，智能体必须参与这个项目）、`task_ids`（只能是本项目的任务）、`use_template`（正文为空时套这种类型的模板）。保存前过密钥扫描（`422 SECRET_BLOCKED`） |
| `GET /api/v1/projects/{project_id}/issues/{number}` | 按项目内编号读（正文里的 `#编号` 链到这里） |
| `GET /api/v1/issues/{issue_id}` | 详情：正文、评论与事件合在一起的时间线、指派、标签、关联任务、正文与评论里 GitHub PR 链接的状态（`pulls`，只用已接入仓库的同步数据）、`can_close`、`can_moderate` |
| `PATCH /api/v1/issues/{issue_id}` | 修改（member，带 `version`，不一致 `409 VERSION_CONFLICT`）：标题、正文、类型、标签、指派、关联任务；`state = closed` 要带 `close_reason`（`resolved` / `wontfix` / `duplicate`，重复时 `duplicate_of` 写编号）；`locked` 只有 owner / admin |
| `DELETE /api/v1/issues/{issue_id}` | 删除（owner / admin，软删除，移出搜索）；审计 `issues.issue.deleted` |
| `POST /api/v1/issues/{issue_id}/comments` | 评论（member，1 ~ 10000 字，可带 `reply_to`）：`@名字` 提到本工作空间的人或项目里的智能体，`#编号` 链到同一项目的 Issue（那边的时间线记“被提到”） |
| `PATCH /api/v1/issues/{issue_id}/comments/{comment_id}` | 编辑自己的评论（记 `edited_at`） |
| `POST /api/v1/issues/{issue_id}/comments/{comment_id}/hide` | 隐藏评论（owner / admin，不物理删除） |
| `POST /api/v1/issues/{issue_id}/read` | 记为已读（viewer 也可以，只改自己的“读到哪里”） |
| `GET /api/v1/issues/inbox` | 我的 Issue 收件箱 |
| `GET /api/v1/tasks/{task_id}/issues` | 任务关联的 Issues（任务抽屉的“讨论”标签） |
| `PUT /api/v1/projects/{project_id}/issue-notices` | `{enabled}`（owner / admin，默认关闭）：Issue 新开、关闭时在项目群里发一行提示；审计 `issues.notices_enabled` / `issues.notices_disabled`；`ProjectOut.issue_notices` |

- 通知：人进通知中心（`issue.activity`：被提到、被指派，自己开的或指派给自己的有新评论）；全局搜索增加 `type=project_issue`（标题是“#编号 标题”，正文含标签）；SSE 增加主题 `issues`。
- 不与 GitHub Issues 双向同步（D19）。

**MCP 工具**（只在自己参与的项目里，`project_agents`；没参与或别的工作空间一律 `NOT_FOUND`，只读参与的写操作 `READ_ONLY`）：

- 读（风险级别 `read`）：`issue_list`（`project_id`、`state` 默认 `open`、`kind`、`label`、`assigned_to_me`、`mentioned_me`、`query`、`limit` ≤ 50；带 `open_count` / `closed_count`）、
  `issue_get`（`issue_id`，或 `project_id` + `number`；评论每页 50 条，用 `comment_offset` 翻页；读完记为已读）、`issue_inbox`（提到你的、指派给你的、你开的，有没读过的动态）。标题、正文、评论、名字都在 `untrusted` 里。
- 直接执行（风险级别 `direct`，例外五）：`issue_create`（字段同页面接口，指派只能是管理员与参与项目的智能体）、`issue_comment`（返回 `agent_comments_left`）、
  `issue_update`（关闭 / 重新打开只限自己开的或指派给自己的，改标题、改类型、加减标签、加减指派、关联 / 取消关联任务；可带 `version`）。没有锁定、隐藏、删除的工具。
- 保护：一个 Issue 里连续 10 条评论都来自智能体（中间没有人评论）时，智能体在这个 Issue 里暂停评论（`ISSUE_PAUSED`），人评论后恢复；锁定的 Issue 智能体不能评论（`ISSUE_LOCKED`）；
  每个实例每小时最多开 10 个 Issue、发 60 条评论（`RATE_LIMITED`）；内容像密钥 `SECRET_BLOCKED`；`issue_create` / `issue_comment` 带 `client_key` 时，同一实例 24 小时内重试返回第一次的结果（`already_created` / `already_commented`）；
  执行记录里正文只记长度；项目打开了提示时同样在项目群里发一行。
- 唤醒：智能体被提到、被指派，或它开的 / 指派给它的 Issue 有新评论时进收件箱，并合并成一次唤醒（`source = issue`，独立会话，5 分钟后执行；还没执行时只累加条数）。
  提示词只写“有 N 条 Issue 动态，请用 issue_inbox 查看”，不含别人写的内容。没配 `wakeup.mjs` 的实例只能在下次运行时自己查。
- 与任务：`get_task` 带上关联的、未关闭的 Issue（`issues`）；`complete_task` 遇到关联的、未关闭的“阻塞”Issue 返回 `TASK_BLOCKED`（人照常可以完成）；`release_task` 的 `next_step` 提醒开“交接”Issue 或更新任务 README。

## 运行服务器 / 容器服务器（T-0136 ~ T-0138；读数与列表 T-0184 / T-0185）

页面上叫“容器服务器”（T-0186，`docs/v2/container-server-redesign/`），地址 `/runner`、接口 `/api/v1/runner/*`、工具名与表名不变。
方案 `docs/v2/RUNNER_PLAN.md`，核验记录 `docs/decisions/2026-11-06-runner-verification.md`、`docs/decisions/2026-11-09-runner-container-metrics.md`；`docs/AGENTS.md` 第 8 节例外八。
管理员接口只给 owner / admin（member、viewer `403`），全部按当前工作空间过滤；V1 每个工作空间一台运行服务器。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/runner/overview` | 运行服务器（`status` 超过 90 秒没有长轮询显示 `offline`、版本、`runtimes`、`kvm`、CPU / 内存 / 磁盘、最近一次对账汇总 `report`、生效的回收策略 `policy`、`version`）、按状态的环境数、每个实例的用量与限额、最近停止 / 删除的环境与原因 |
| `POST /api/v1/runner/host` | 添加运行服务器：`name`、`ingress`（`intranet` / `public`）；只建记录，凭证在下载接入包时签发。已有一台 `409 RUNNER_EXISTS`；这个入口没配置 `409 AGENT_INGRESS_NOT_CONFIGURED`；审计 `runner.host_created` |
| `POST /api/v1/runner/host/bundle` | 下载接入包（zip，`Cache-Control: no-store`）并签发新凭证（`mwr_` 开头，工作台只存 SHA-256），旧凭证立即失效；外网入口另签客户端证书（实例的内部 CA）。内容：`runner.env`（600）、`compose.yaml`（锁定 digest 的 python 镜像）、`agent/runner_agent/*.py`、`install.sh` / `install-docker.sh`（Docker 29.9.1）/ `install-gvisor.sh`（release-20261019.0，核对 sha512）、`tls/`、`CHECKLIST.md`；审计 `runner.host_reissued` |
| `DELETE /api/v1/runner/host?version=` | 移除；还有没删除的环境 `409 RUNNER_HAS_ENVIRONMENTS`；审计 `runner.host_removed` |
| `PUT /api/v1/runner/host/policy` | 回收策略覆盖值 `policy`（键见 `app/domain/runner_specs.py` 的 `DEFAULT_POLICY`，不认识的键 `422`）、`version`；审计 `runner.policy_updated` |
| `GET /api/v1/runner/host/ssh` | 通过 SSH 部署运行服务器（**owner**，T-0136）：目标服务器与最近一次部署 / 卸载任务（结构与 OpenClaw 实例的 SSH 自动部署相同：`AgentSshState`）；还没有运行服务器时 `target`、`job` 都是 `null`（写接口要先添加运行服务器，否则 `404`） |
| `PUT /api/v1/runner/host/ssh` | 填写或更换 SSH 地址、账号：只读取主机公钥（不登录），返回 SHA256 指纹；更换要带 `version`；每个用户每分钟最多探测 10 次（`429`）；连不上 `503 AGENT_SSH_UNREACHABLE`；已装专用公钥 `409 AGENT_SSH_KEY_INSTALLED`；审计 `runner.ssh.target_set` |
| `POST /api/v1/runner/host/ssh/confirm` | owner 在服务器上核对后确认指纹（`version`、`fingerprint`）；审计 `runner.ssh.host_key_confirmed` |
| `POST /api/v1/runner/host/ssh/install` | 部署 / 重新部署（`202`）：`version`、`password`（只随这一次请求，不保存；第一次必填，不是 root 账号时每次都要，因为 `install.sh` 用 sudo 运行）。步骤 connect → inspect → authorize_key → upload（签发新凭证、上传接入包到 `~/myworkspace-runner`）→ install（以 root 运行 `install.sh`）→ online（runner-agent 用新凭证连上）；日志里密码、凭证、私钥替换成 `***`；审计 `runner.ssh.install_started` / `install_succeeded` / `install_failed` |
| `POST /api/v1/runner/host/ssh/uninstall` | 停止 runner-agent（`202`）：`version`、`remove_files`、`password`（非 root）；还有没删除的环境 `409 RUNNER_HAS_ENVIRONMENTS`；Docker 与 gVisor 保留 |
| `POST /api/v1/runner/host/ssh/revoke` | 吊销专用密钥（`version`、`force`）：用它登录删掉 `authorized_keys` 里的那一行，再删工作台保存的私钥；连不上时 `force=true` 只删私钥并返回 `warning` |
| `DELETE /api/v1/runner/host/ssh?version=` | 删除 SSH 地址（`204`）；装着专用公钥时 `409`，先吊销 |
| `GET /api/v1/runner/environments` | 环境列表：`state`、`instance_id`、`lifetime`、`include_deleted`，T-0185 加 `q`（用途或环境 ID 包含查询，1–100 字，`%` / `_` 按字面）、`sort`（`last_used` 默认 / `cpu` / `memory`：实时读数高的在前，过期、未知、未运行排最后，同分按 ID）、`cursor`（≤ 512，绑定工作空间、全部筛选、排序与每页条数，不匹配 `422`）、`limit`（1–500，缺省 500 兼容旧页面）；响应加 `total`（同样筛选下的全部匹配数，在分页前计算）与 `next_cursor`。每项含智能体写的用途 `untrusted.purpose`、配置记录 `spec`、运行时 / 用户 / 网络、内存与磁盘占用、出口访问记录 `egress.hosts`（`域名:端口` → 次数、字节、拒绝次数）、删除条件、`next_step`、`wake_seconds`、保留分 `retention_score`（分低的先回收；只算运行中与已停止的环境）与 `retention_infinite`（无穷大：常驻环境的数据超过快照上限、重建不出来，只会被停止、不会被自动归档）、快照大小与时间、归档时间、`version`；T-0185 起每项带 `metrics`：`cpu_used_cores` / `cpu_percent_of_limit` / `cpu_window_seconds` / `cpu_reported_at` / `cpu_status`、`memory_used_bytes` / `memory_percent_of_limit` / `memory_reported_at` / `memory_status`、`data_volume_bytes` / `data_volume_reported_at` / `data_volume_status`；状态 `ok`（有值、上报时间在 180 秒内、主机在线且没停用）/ `stale`（有旧值，保留数字与时间，时间可能未知）/ `unavailable`（没有值）/ `not_running`（不在运行的环境的 CPU / 内存）；百分比按配置的 `cpus`、`memory_mb × 1024²` 算，可以超过 100 |
| `GET /api/v1/runner/environments/{id}` | 一个环境的详情（T-0185），与列表项相同；不在列表筛选里也能读；其他工作空间与不存在的都 `404`；只读本地记录，不连 Docker / SSH |
| `POST /api/v1/runner/environments/{id}/actions` | `action` ∈ `start` / `stop` / `archive`（常驻先停止、做快照，完成后才归档；被保护的 `409`）/ `delete`（立即进入待删除；常驻先做快照，快照保留 30 天）/ `keep`（撤销待删除：回到停止，归档过的回到归档）/ `restore`（归档的按配置记录重建；已删除的常驻环境在快照保留期内从快照恢复，没有快照 `409 SNAPSHOT_NOT_FOUND`）/ `pin` / `unpin`（保护：不被自动回收），带 `version`；审计 `runner.environment_<action>` |
| `GET /api/v1/runner/templates` | 模板（`RunnerTemplateAdminOut`：谁建的、`untrusted.description`、规格、构建状态、构建日志的结尾 `build_log`（最多 20000 字，已脱敏）、镜像 digest、构建耗时、镜像大小、使用次数、乐观并发的 `version`）；新建、修改、改状态、重新构建都返回同样的结构 |
| `PUT /api/v1/runner/templates` | 管理员新建 / 修改模板（`name` 只能是小写字母数字与 `._-`）：规格变了版本号加一并下发构建；修改带 `version`；审计 `runner.template_saved` |
| `PUT /api/v1/runner/templates/{id}/status` | `active` / `disabled`，带 `version`；审计 `runner.template_status` |
| `POST /api/v1/runner/templates/{id}/rebuild` | 重新构建；审计 `runner.template_rebuild` |
| `GET /api/v1/runner/runs` | 运行记录：`environment_id`、`instance_id`、`limit`（≤ 200）；输出只返回最后 16 KB |
| `GET /api/v1/runner/runs/{id}` | 一次运行的完整输出（最多 1 MB，已按注入的密钥值与密钥扫描脱敏，放在 `untrusted.output`） |
| `GET /api/v1/agents/instances/{id}/runner` | 实例的运行环境权限：`enabled`、生效的 `limits`、可以注入的密钥（密钥库已授权给这个实例的名称）、`enabled_at`、`version`（实例的） |
| `PUT /api/v1/agents/instances/{id}/runner` | `enabled`、`limits`（`envs`、`persistent`、`memory_mb`、`cpus`、`disk_gb`、`execs_per_hour`、`run_hours_per_day`；不认识的键 `422`）、`version`。只读实例打不开（`422`）；审计 `agents.instance.runner_updated`；打开时通知管理员 `runner.instance_enabled`；关掉时停止它所有运行中的环境 |

**运行服务器接口**（只在接入入口开放，`/runner-api/v1/*`，Caddy 请求体上限 64 MB）：认证是运行服务器凭证 + 入口请求头
（只在请求直接来自接入入口时采用；外网另要客户端证书指纹一致），失败一律 `401 RUNNER_UNAUTHORIZED`，外网入口每个 IP 15 分钟 10 次失败后 `429`。

| 方法与路径 | 说明 |
|---|---|
| `POST /runner-api/v1/poll` | 上报版本与容量（`runtimes`、`kvm`、CPU、内存、磁盘），长轮询领指令（`wait_seconds` ≤ 25，一次最多 10 条，`FOR UPDATE SKIP LOCKED` 后标为 `sent`）。需要注入密钥的指令（`create_env` / `start_env` / `exec`）**这时才解密**放进 `payload.secret_values`（不落库，每次写取密钥记录）；`no-store` |
| `POST /runner-api/v1/commands/{id}/result` | `status`（`done` / `failed`）、`result`、`error`；重复回报只认第一次（`accepted: false`）；不是这台服务器的指令 `404` |
| `POST /runner-api/v1/report` | 每分钟对账：带工作台标签的容器、数据卷、镜像与占用、没有标签的容器数（只计数）、出口访问记录。T-0184 起容器项可带 `cpu_used_cores` 与 `cpu_window_seconds`（同时有值或同时为空；有限、非负、窗口 1–180 秒，不合规整份报告 `422`），旧 runner-agent 不带也照常对账；`mem_bytes` 读不到时为空（不再报 0），数据卷 `bytes` 为负数时按未知处理。返回 `lost`（记录里有、实际没有：标为 `failed` 并通知管理员）与 `stray`（实际有、记录里没有：runner-agent 先停止、24 小时后删除） |
| `PUT /runner-api/v1/snapshots/{id}/parts/{n}` | 上传快照的第 `n` 块（`application/octet-stream`，≤ 16 MB，`n` < 100000）。工作台用这份快照自己的数据密钥（AES-256-GCM，附加数据“快照 ID:块号”）加密后存进对象存储；只认这台服务器、状态是上传中的快照（否则 `404` / `409 SNAPSHOT_CLOSED`），存储不可用 `503 STORAGE_UNAVAILABLE` |
| `GET /runner-api/v1/snapshots/{id}/parts/{n}` | 恢复时取第 `n` 块（解密后的原文，`no-store`）。快照没就绪 `409 SNAPSHOT_NOT_READY`；没有这一块 `404`；校验失败 `500 SNAPSHOT_CORRUPT` |

指令：`build_template`、`create_env`（带 `snapshot` 时先恢复快照，带 `template_build` 时模板镜像不在就先重建）、`start_env`、`stop_env`、`delete_env`、`archive_env`（删容器、网络与数据卷，只留配置记录）、`snapshot`（环境停着时按“要保存的目录”打 tar、按块上传，`then` 是完成后接着 `archive` 还是 `delete`）、`prune_images`（删掉不在 `keep` 里、带工作台标签的模板镜像）、`exec`、`put_files`、`get_files`、`list_files`。
时限到了还没结果：能重发的（停止、删除、归档、读文件）最多重发 3 次；快照记为失败（环境保持停止，不归档也不删除，通知管理员）；`exec` 记为结果未知、不自动重跑（运行记 `unknown`，有唤醒时叫醒智能体）；建环境、构建模板记为失败。

**MCP 工具**（只列给打开了运行环境权限的实例，关掉后调用 `FORBIDDEN_CAPABILITY`；直接执行、不审批）：

- 读：`runner_templates`、`env_find`、`env_run`。
- 写（风险级别 `direct`）：`runner_template_save`（保存后构建，最多等 `wait_seconds`）、`env_create`（必填 `purpose`、`lifetime`；`template` 或 `spec`；有安装步骤的规格要先建模板；同一个 `reuse_key` 先复用；`lifetime=once` 必须带 `command`，跑完就删；`delete_when`、`share=project`、`files`、`from_environment`）、
  `env_exec`（`wait_seconds` 内结束直接返回退出码与输出的最后 16 KB，否则返回 `run_id`、结束后用唤醒叫醒；超时停止环境；每小时 120 次）、`env_files`（`list` / `read` / `write`，单个文件 20 MB；经 MCP 的请求体上限 32 MB）、`env_setup`、`env_update`（`keep=true` 撤销待删除）、`env_stop`、`env_release`。
  对归档的环境调用 `env_exec` / `env_files` 会按配置记录自动重建（常驻的先恢复快照，再重放初始化步骤）。
  新建时整台服务器的内存不够：先按保留分停掉空闲的环境，这次返回 `RESOURCE_BUSY`（“已按保留分停止 N 个空闲环境，约 30 秒后再试”）；腾不出来、或磁盘超过 95% 时也是 `RESOURCE_BUSY`。
- 用途、模板说明、命令输出在 `untrusted` 里。错误码：`RUNNER_UNAVAILABLE`（没有运行服务器或不在线）、`QUOTA_EXCEEDED`、`RESOURCE_BUSY`、`RATE_LIMITED`、`RUNNER_CAPABILITY`（例如没有 `/dev/kvm`、没装 gVisor）、
  `TEMPLATE_NOT_READY`、`ENV_UNAVAILABLE`、`TIMEOUT`、`RUNNER_FAILED`、`FORBIDDEN`、`NOT_FOUND`、`VALIDATION_FAILED`、`PAYLOAD_TOO_LARGE`。

## 插拔式模块（T-0159）

开发指南 `docs/modules/README.md`，决定见 `docs/decisions/2026-11-08-feature-modules.md`。模块按工作空间启用；
模块自己的页面接口都挂在 `/api/v1/<key>/...`，宿主给每个接口加启用检查。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/modules` | viewer。所有模块与在当前工作空间的状态：`key`、`name`、`description`、`version`（清单的发布版本）、`enabled`、`state_version`（启用状态的版本，实际启停一次加 1，没有记录时 0）、`default_enabled`、`ready`（部署前提是否满足）、`missing`（没设置的 `.env` 变量名）、`depends_on`、`dependents`、`tools`（给智能体的工具）、`widgets`（首页小组件类型）、`can_manage`（只有 owner 为 true） |
| `PATCH /api/v1/modules/{key}` | owner。`{"enabled": true/false, "expected_state_version"?: n}`（多余字段 `422`；`key` 格式不对 `422`，不存在 `404`）。依赖的模块没启用、或有启用的模块依赖它时 `409 MODULE_DEPENDENCY`；带 `expected_state_version` 且与当前不同 `409 VERSION_CONFLICT`；同一工作空间正在启停或重试 `409 MODULE_BUSY`（不写任何东西）。状态真的变化时 `state_version` 加 1、审计 `module.enabled` / `module.disabled` 并发 `modules`、`overview` 事件，然后执行模块的同步钩子（结果写进同步记录，失败不回滚开关）；重复设置同一个值不再记审计、不执行钩子。停用不删除模块数据，再启用就恢复。缺部署前提（`ready=false`）也可以启用 |
| `GET /api/v1/modules/management` | owner / admin。应用中心：所有模块的管理信息 `{items, summary: {registered, enabled, attention}, generated_at}`，不分页；`summary` 基于全部模块，与搜索无关。只读注册表、数据库与健康缓存，不探测外部服务（T-0210） |
| `GET /api/v1/modules/{key}/management` | owner / admin。一个模块：`module`（`ModuleOut`，含 `state_version`）、`host_api`、`capabilities`（工具名与风险、首页组件、定时任务 `{id, kind, schedule_summary}`、实时事件、依赖 / 被依赖及启用状态、外部服务、`has_sync_hook`；不输出导入路径）、`health`（`state`：`ok` / `degraded` / `down` / `unknown` / `not_configured` / `not_applicable`，`checked_at`、`stale`、`checks`）、`sync`（`state`：`not_required` / `unrecorded` / `pending` / `running` / `succeeded` / `failed` / `unknown` / `superseded`，`state_version`、`attempt_id`、`target_enabled`、`trigger`、`reason` 固定类别、时间）、`attention`、`actions`（`can_toggle` / `can_retry_sync` / `can_check_health`）。未知模块 `404` |
| `POST /api/v1/modules/{key}/health-check` | owner / admin + CSRF。只探测清单声明的服务（不接受地址或参数），单个最多 10 秒、同时 3 个；每个工作空间每个模块每分钟 3 次，超过 `429 RATE_LIMITED`（`Retry-After`）。停用 `409 MODULE_DISABLED_FOR_CHECK`；没声明服务 `not_applicable`；没部署 `not_configured`、不出站；检测失败也是 `200`（`down`）。返回 `ModuleHealthOut` |
| `POST /api/v1/modules/{key}/reconcile` | owner + CSRF。`{"expected_state_version": n}`。按当前目标重新执行同步钩子，不改启用状态、不自动启用依赖；返回 `200 ModuleManagementOut`（成功 / 失败 / 未知都在 `sync`）。版本过期 `409 VERSION_CONFLICT`（`details.current_state_version`）；正在启停或重试 `409 MODULE_BUSY`；没有钩子返回 `not_required`、不写记录。审计 `module.sync.retried` 与结果 |
| `GET /api/v1/modules/{key}/activity`、`GET /api/v1/modules/activity` | owner / admin。模块的操作记录（启用 / 停用、同步结果与重试），审计日志按 `resource_type=module` 与 `resource_id=key` 精确过滤，新的在前，`limit`（1–100，默认 30）与 `cursor`；游标绑定工作空间与模块，换模块 `422`；不返回总数。不带 key 时列出所有模块（含已移除模块的历史） |
| 模块接口 | 模块在请求头的工作空间里停用时，模块的所有页面接口返回 `404 MODULE_DISABLED`（先做成员检查：不是成员仍是 `404 NOT_FOUND`）。智能体工具不再列出，调用返回 `NOT_FOUND`；运维页不显示该模块的外部服务；模块自己的内部接口与定时任务也停止（新闻早报的接收地址返回 `404`） |

## 侧栏布局与零代码页面（T-0161）

每个工作空间一份布局：自定义分组、把已有页面挪到别的分组、零代码页面。分组 key：内置 `workspace` / `tools` / `admin` / `apps`，
自定义 `custom-<id>`。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/navigation` | viewer。`version`（还没改过是 0）、`sections`、`pages`（只返回当前角色能看到的，按 `min_role`）、`placements`（`{站内路径: 分组}`）、`section_order`、`embed_origins`（部署允许嵌入的来源）、`can_manage`（只有 owner 为 true） |
| `PUT /api/v1/navigation` | owner。整份替换 + `version`（不一致 `409 VERSION_CONFLICT`）。最多 12 个分组、50 个页面、100 条挪动。页面 `kind`：`link`（`url`，只允许 http(s)、不能带账号密码，新窗口打开）、`page`（`path`，以 `/` 开头的站内路径）、`note` / `dashboard`（`target_id`，必须属于当前工作空间）、`embed`（`url`，来源必须在 `.env` 的 `EMBED_ORIGINS` 里且不是工作台自己，页面上用带沙箱的 iframe 显示）。引用不存在的分组、重复 id、多余字段都 `422`。审计 `navigation.updated`，发 `navigation` 事件 |

## 首页改版（T-0163）

方案 `docs/v2/HOME_PLAN.md`（首页效果图“工作总览”）。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/tasks/progress?status=&limit=` | viewer。`status`：`all`（默认）/ `todo` / `running` / `awaiting_view` / `blocked`；`limit` 1–20（默认 7）。未归档项目里任务的四种状态，与任务页同一判定（T-0166，见下一节）：已完成待查看（按当前用户）、阻塞（有人写了原因）、进行中（有正在执行的一段）、待办。返回 `counts`（`all` / `todo` / `running` / `awaiting_view` / `blocked`）、`attention`（待查看 + 阻塞）、`items`（`id`、`title`、`project_id`、`project_title`、`status`、`executor`（`kind` user / instance、`id`、`name`，没有指派为 null）、`due_at`、`updated_at`、`blocker`（`reason`、`blocked_at`、`blocked_by`、`blocked_by_name`））。全部时按 待查看 → 阻塞 → 进行中 → 待办 排，同状态最近更新的在前；已完成且你已查看的任务不出现 |
| `GET /api/v1/tasks?assignee=me` | viewer。只看执行者是当前用户的任务（首页“我的待办”与 `status=open` 一起用） |
| `POST /api/v1/tasks` | 新增可选字段 `assignee_user_id`：必须是本工作空间成员（否则 `422`），首页“添加待办”指派给自己 |
| ~~`POST /api/v1/tasks/{id}/review`~~ | T-0166 删除：改为每个人自己“标为已查看”（`PUT /tasks/{id}/result-view`） |
| ~~`TaskOut.review_pending`~~ | T-0166 删除：由 `completion_seq`、`result_viewed`、`work_state` 取代 |
| `POST /api/v1/dashboards/{id}/reset` | member（能编辑仪表盘的人）。`{"version"}`。删掉这个仪表盘的全部组件，换成默认首页的五个组件（工作概况、任务进展、AI 新闻速览、我的待办、提醒与日程），版本 +1，审计 `dashboard.reset`；版本不一致 `409` |
| 组件类型 | 新增 `work_summary`（`reminder_days` 1–14，默认 2）、`task_progress`（`limit` 1–20，默认 7）、`my_todos`（`limit` 1–20，默认 5）；`calendar_agenda` 前端改为“提醒与日程”时间轴，`news_briefing` 改为“AI 新闻速览”（今天早报的主题，`GET /api/v1/news/today`） |

## 四页改造：任务状态、项目统计（T-0166 ~ T-0168）

方案 `docs/v2/pages-redesign/`，决策 `docs/decisions/2026-11-09-task-work-states.md`。五种互斥状态 `work_state`：
`todo`（待办）、`running`（有未结束的执行段）、`awaiting_view`（已完成、你还没查看这一轮结果）、`blocked`（有人写了阻塞原因）、`done`。
“待查看”按人隔离；迁移前完成的任务对所有人都是 `done`。

| 方法与路径 | 说明 |
|---|---|
| `TaskOut` 新字段 | `work_state`、`completion_seq`（完成轮次，迁移前完成的为 0）、`result_viewed`（你看过当前这一轮）、`active_run`（`id`、`executor_kind`、`executor_name`、`started_at`，没有执行中的段为 null）、`blocker`（`reason`、`blocked_at`、`blocked_by`、`blocked_by_name`，没阻塞为 null） |
| `GET /api/v1/tasks` 新参数 | `work_state`（按你的状态筛选，与 `status` 取交集；按状态看时传 `status=all`）、`assignee_user_id`、`assignee_instance_id`（两者只能给一个，也不能和 `assignee=me` 同时给，否则 `422`） |
| `GET /api/v1/tasks/summary` | viewer。与列表同样的筛选（不含 `status` / `work_state`），返回 `todo` / `running` / `awaiting_view` / `blocked` / `done` / `total` / `as_of`，供状态筛选标签用 |
| `PUT /api/v1/tasks/{id}/result-view` | viewer。`{"completion_seq"}`：把这一轮记为你已查看，只影响你自己，同一轮重复调用幂等。任务已重新打开或又完成了一次：`409 COMPLETION_CHANGED`，`details.current` 是最新的任务。打开详情不会自动标记 |
| `PATCH /api/v1/tasks/{id}/block` | member，必须带 `Idempotency-Key`。`{"version", "blocked", "reason"}`：`blocked=true` 时原因 1–2000 字，`false` 清除。不停止正在执行的一段，解除也不自动重新开始。已完成 `409 TASK_DONE`；版本不一致 `409 VERSION_CONFLICT`。动态记 `blocked` / `unblocked`，审计 `task.blocked` / `task.unblocked` |
| 领取、开始执行 | 阻塞的任务不能被智能体领取（`claim_task` 返回 `TASK_BLOCKED`），也不能开始新的一段（`POST /tasks/{id}/runs`、交给智能体执行：`409 TASK_BLOCKED`），先解除 |
| 完成与打回 | 每次未完成→完成 `completion_seq` 加 1，并清掉阻塞；人自己勾完成时同时记自己已查看，智能体（`complete_task`、审批通过的完成）或别人完成时所有人都进入待查看。打回（`/reopen`）不加轮次，再完成才加 |
| `GET /api/v1/projects` 新参数与字段 | `attention_only`（有你待查看或阻塞任务的项目）、`archived_only`、`sort=updated`（按最近更新）；`ProjectOut.task_states`（五种状态各几项，按你的查看状态）、`last_activity_at`（项目本身或其中任务最后一次修改的时间，取较晚者）。`open_tasks` / `done_tasks` 口径不变 |
| `GET /api/v1/projects/summary?completed_after=&completed_before=` | viewer。`active`（未归档项目数）、`attention`（需要你关注的项目数）、`completed`（`done_at` 在 `[after, before)` 内且现在仍完成的任务数）、`as_of`。前端按浏览器时区算“本周”（周一 00:00 到下周一 00:00） |
| `GET /api/v1/projects/activity?project_id=&cursor=&limit=` | viewer。任务动态（设为可领取、领取、放弃、完成、打回、标记 / 解除阻塞），新的在前，keyset 游标；`project_id` 不填是本工作空间的全部项目。返回 `id`、`kind`、`actor_kind`、`actor_name`、`note`（人或智能体写的，按纯文本显示）、`task_id`、`task_title`、`project_id`、`project_title`、`created_at` |

## 新闻早报（T-0153）

方案 `docs/v2/NEWS_PLAN.md`，核验记录 `docs/decisions/2026-11-07-trendradar-verification.md`。新闻早报是插拔式模块 `news`（默认启用，owner 可在应用中心停用，停用后下面的接口都是 `404 MODULE_DISABLED`）。整个部署只有一个 TrendRadar：
同一时刻只能有一个工作空间启用。没有部署（`TRENDRADAR_MCP_URL` 为空）时设置照样能保存，`deployed=false`，不写配置文件。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/news/today` | viewer。今天（按部署的时区 `TRENDRADAR_TZ`）最新一份早报 + 每个兴趣主题今天的条目（经 MCP 查 `get_news_by_date` 与 `get_latest_rss`，按主题关键词、必含词、排除词匹配；当前用户标了不感兴趣的不在这里）。TrendRadar 连不上时 `unavailable=true`，早报照常返回 |
| `GET /api/v1/news/status` | viewer。`deployed`、`enabled`、`online`（MCP `get_system_status`）、`latest_data_date`、`latest_briefing_at`、`rendered_at`、`render_error` |
| `GET /api/v1/news/briefings?cursor&limit` | viewer。往期早报（`excerpt` 是正文开头的纯文字），游标分页 |
| `GET /api/v1/news/briefings/{id}` | viewer。一份早报：`local_date`、`slot`、`source`（`trendradar` / `agent`）、`title`、`content`（Markdown，按安全模式渲染）、`status`（`receiving` / `complete` / `pending` / `failed`）、`parts` / `total_parts`、`error`、`task_id` |
| `GET /api/v1/news/hot?platform&limit` | viewer。最新一批热榜（MCP `get_latest_news`）；`platform` 只能是核验过的 11 个 id |
| `GET /api/v1/news/rss?feed&days&limit` | viewer。最近几天的 RSS（MCP `get_latest_rss`） |
| `GET /api/v1/news/search?q&days&include_rss&limit` | viewer。按关键词搜热榜（MCP `search_news`）与 RSS（`search_rss`；`search_news` 在热榜没有命中时不带 RSS），日期范围按部署的时区 |
| 条目 | `title`、`url`、`mobile_url`（只保留 http(s) 链接，别的整条丢掉）、`source_type`（`hotlist` / `rss`）、`source_id`、`source_name`、`rank`、`time`、`summary`、`topic`（命中的主题）、`favorite_id`、`dismissed`；没有数据时 `items=[]` 且有 `message` |
| `GET /api/v1/news/feedback?kind&cursor&limit` | viewer。我的收藏 / 不感兴趣 |
| `POST /api/v1/news/feedback` | member（`201`）。`kind`（`favorite` / `dismiss`）、`url`（http(s)）、`title`、`source_type`、`source_id`、`source_name`、`topic`；同一条重复提交返回同一条记录；收藏与不感兴趣互斥。`exclude_word`（只有 owner，否则 `403`）把一个词加进全局排除并重新生成配置文件，审计 `news.exclusion_added` |
| `DELETE /api/v1/news/feedback/{id}` | member（`204`）。只能删自己的，别人的 `404` |
| `GET /api/v1/news/settings` | admin。设置（还没保存过返回默认值，`version=0`）；密钥只回 `ai_api_key_set` 与每个渠道的 `secrets_set`；`platform_choices`、`taken_by_other`、`learned`（主题分数与调整后的条数、建议关掉的来源） |
| `PUT /api/v1/news/settings` | owner。整份设置 + `version`（不一致 `409 VERSION_CONFLICT`）。另一个工作空间已启用 `409 NEWS_TAKEN`。地址（RSS、NewsNow、大模型接口、推送 Webhook、ntfy 服务器）只允许公网 http(s)，邮件的 SMTP 服务器同样检查：回环、私有、链路本地、IPv4 简写、没有点的主机名，以及解析到这些地址或解析不了的域名都 `422`（`NEWS_ALLOWED_HOSTS` 例外）。存的渠道凭证解不开时（换了加密密钥）按未设置处理，重新填就能保存。`ai_api_key` 不给表示不改、空字符串清除；渠道的 `secrets` 同理；启用的渠道缺凭证 `422`。保存后重新计算偏好、按启用的工作空间重新生成配置文件；审计 `news.settings_updated`（不含密钥） |
| `POST /api/v1/news/settings/test-push` | owner。经 TrendRadar MCP `send_notification` 推到启用的渠道（标题以“【工作台】”开头），返回每个渠道的结果；`409 NEWS_DISABLED` / `NO_CHANNELS`，`503 NEWS_NOT_DEPLOYED` / `EXTERNAL_UNAVAILABLE`，`504 RESULT_UNKNOWN`；审计 `news.test_push` |
| `POST /api/v1/news/briefings/generate` | owner（`201`）。方案 B：现在建任务、叫醒智能体写一份；`409 NEWS_DISABLED` / `NOT_AGENT_MODE` / `BRIEFING_PENDING` |

**接收早报**（内部网络，不经 Caddy）：`POST /internal/news/ingest/{token}`，TrendRadar 通用 Webhook 的每一批
`{"title": "<报告类型>", "content": "<Markdown>"}`。token 不对、没有启用一律 `404`；单批超过 64 KB `413`；每个工作空间每分钟 60 批（`429`）。
按 `**[第 i/N 批次]**` 拼接：第 1 批开始新的一份（上一份没收齐的记为 `failed`），重复批次返回 `duplicate`，缺批在正文里注明；
收齐后 `status=complete`、通知 owner（`news_briefing`）。标题以“【工作台】”开头的返回 `ignored`。正文最多 20 万字。

**MCP 工具**（只读、不审批；只列给启用了早报、部署了 TrendRadar 的工作空间的实例，否则调用 `NOT_FOUND`）：
`news_latest`、`news_search`、`news_by_date`（最多 31 天）、`news_trending`、`news_rss`、`news_related`、`news_briefing`。
标题、链接、来源名、摘要、早报正文放在 `untrusted` 里；TrendRadar 连不上 `EXTERNAL_UNAVAILABLE`。工作台只转发白名单里的 TrendRadar 只读工具，
`trigger_crawl`、`send_notification`、`read_article` 等不开放。

**SSE 主题** `news`：早报送达、设置与收藏变化。**首页组件** `news_briefing`（`config.lines` 2 ~ 20）。

## 审计查询与导出（T-0197）

方案 `docs/v2/ai-admin-redesign/`（接口约定第 5 节）。全部 owner / admin，按 `X-Workspace-Id`；member / viewer `403`，非成员 `404`。
只读：审计记录仍然只追加（数据库触发器拒绝 UPDATE），没有删除或修改记录的接口；列表与详情不写审计。
不属于任何工作空间的记录（`workspace_id` 为空）永远不出现。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/audit-logs` | 原接口加筛选，仍是 `Page`（`limit` 默认 30、1–100，`cursor`，`total_estimate` 为 null）。`after` / `before`（带时区、成对，`after` 含、`before` 不含且 `after < before`）、`actor_id` 或 `system_only=true`（只看 `actor_id` 为空的系统事件，二者互斥）、`action`（精确）或 `action_prefix`（按字面的前缀，二者互斥，≤128）、`resource_type`（≤64）、`q`（1–100，按字面、不区分大小写匹配 `action` / `resource_type` / `resource_id` 与 metadata 顶层的 `name` / `title`，以及内置事件的中文名；不搜 IP 哈希与其他 metadata）。筛选都在 SQL 里、分页之前生效。`next_cursor` 带签名，绑定工作空间与筛选条件：换了条件或篡改 `422 VALIDATION_FAILED`，从第一页重新加载 |
| `GET /api/v1/audit-logs/{id}` | 一条记录（`AuditLogView`）；别的工作空间或没有工作空间的记录 `404` |
| `GET /api/v1/audit-logs/export` | 同样的筛选，必须给 `after` 与 `before`，最长 31 天，不接受 `cursor` / `limit`（`422`）。读本次请求事务开始之前的记录，最多 10000 行：超出 `422 AUDIT_EXPORT_TOO_LARGE`（`details.limit=10000`），不截断。`text/csv; charset=utf-8`、UTF-8 BOM、`Content-Disposition: attachment; filename="audit-logs-YYYYMMDD-YYYYMMDD.csv"`（UTC 日期）、`Cache-Control: no-store`。列：`id`、`created_at_utc`（ISO，`Z` 结尾）、`action`、`action_label`、`actor_id`、`actor_label`、`resource_type`、`resource_id`、`resource_current_name`、`metadata_json`。去掉前导空白后以 `=` `+` `-` `@` 开头、或以制表符 / 回车开头的单元格前面加 `'`。`metadata_json` 再过一遍脱敏，并去掉 IP 类（`ip`、`client_ip`、`source_ip`、`ip_hash` 等）与凭据类的键。生成后在同一个事务里记 `audit.export_generated`：操作者、时间范围、截止点、行数与过滤摘要（搜索词只记“用了”，不记原文；不记文件内容） |

`AuditLogView` 在 `AuditLogOut` 之外多三个可为 null 的字段，都只在当前权限内补充：
`action_label`（内置事件的中文名，未知事件为 null，界面显示原 action；“已发起”类事件不写成成功）、
`actor_label`（当前工作空间成员的显示名；`actor_id` 为空是“系统”；已不是成员为 null，显示 ID）、
`resource_current_name`（对象“现在”的名称，不是当时的快照；只查本地表、批量查：`user`、`workspace`、`project`、`task`、`integration`、`agent_instance`，以及只有 owner 能看到的 `agent_secret`、`agent_server`；对象已删除、不在本工作空间或无权读取为 null）。
列表与详情的 `metadata` 是写入时脱敏的内容，再过一遍脱敏并去掉 IP 类的键；没有通用的“结果”字段，不补前后值或 IP。

## 中转站管理与 OpenClaw 主模型（T-0200 ~ T-0208）

方案 `docs/v2/model-connections/`，决定与 OpenClaw 核验见 `docs/decisions/2026-11-09-model-connections.md`。
全部 owner（admin / member / viewer `403`，非成员或别的工作空间 `404`）；写操作要 CSRF。**任何响应都不含 Key、密文、`credential_ref` 或内部 secret_id。**

连接（`/api/v1/model-connections`）：

| 方法与路径 | 说明 |
|---|---|
| `GET /` | 服务端搜索 `q`（名称、URL、模型 ID）与筛选 `type`、`configuration_state`、`enabled`、`check_status`（含 `never`），分页之前生效；`total` 是全部匹配数。游标绑定筛选条件。每行带 `model_count` 与 `model_preview`（按 ID 排序的前 3 个模型 ID，列表不必逐行再取模型） |
| `GET /capabilities` · `GET /summary` | 已开放的协议、官方预设与限制；全工作空间统计（不受筛选与分页影响；`check_passed` 只计 ready + 启用、15 分钟内对当前配置修订号检测通过的连接） |
| `POST /` | 新建（可带 `Idempotency-Key`）：名称、类型、协议、base URL（只收协议基地址，不收 `/chat/completions` 等单次请求地址、userinfo、query）、`api_key`、模型集合；`save_as_draft` 存草稿。官方预设配第三方主机 `422 MODEL_OFFICIAL_HOST_MISMATCH`，未开放的协议 `422 MODEL_PROTOCOL_UNSUPPORTED` |
| `GET/PATCH/DELETE /{id}` | PATCH 带 `version`；已授权或已绑定的连接改 URL / 协议 `409 CONNECTION_ENDPOINT_IN_USE`（复制为新连接再切换）；停用会影响实例时先 `409 CONNECTION_IN_USE`（`details.impact`），带 `confirm_impact=true` 再提交；有授权、绑定或进行中的应用任务时不能删除（`409 CONNECTION_IN_USE`） |
| `POST /{id}/rotate-key` | 只换 Key（不能和改地址一起）：凭据版本与配置修订号加 1、旧密文清除、检测结果与“已验证”失效，绑定到它的实例重新应用 |
| `GET/PUT /{id}/models` · `POST /{id}/models/sync` | 明确的模型 ID（原样大小写，每条连接最多 1000 个）；`all_available` 用最近一次完整同步的列表；同步不完整或失败保留上一份完整列表，不移除被绑定的模型（`409 MODEL_IN_USE`） |
| `POST /{id}/checks` | `mode=metadata`（列模型）或 `inference`（必须选模型，发一条最短请求，可能产生少量费用）；结果只有固定类别（`passed` / `failed` / `rate_limited` / `unsupported` / `unknown` + 原因），不存远端原文；429 带 `retry_at` |
| `GET/PUT /{id}/grants` · `GET /{id}/activity` | 授权给哪些实例（在用的实例 `409 GRANT_IN_USE`）；创建 / 配置 / 轮换 / 授权 / 取用 / 应用的脱敏记录 |

主模型绑定（`/api/v1/agents/...`）：

| 方法与路径 | 说明 |
|---|---|
| `GET /agents/model-assignments` · `GET /agents/model-connection-options` | 每个实例的目标 / 已应用 / 状态（还没设置主模型的 `binding` 为 null）；可选的连接（启用、配置完整、协议已开放；带 `instance_id` 时标出是否已授权） |
| `GET/PUT/DELETE /agents/instances/{id}/model-binding` | 绑定是目标：保存后 `pending`，实例确认后才 `applied`（`applied_*` 快照单独保存）。PUT 第一次 `version=null`；要求连接启用、配置完整、已授权给这个实例、模型在集合里、Key 可读。DELETE 解除（`revoked=true`，立即拒绝后续取 Key） |
| `POST /agents/instances/{id}/model-binding/apply` · `GET .../jobs/{job_id}` | （重新）应用当前目标：同一修订号复用同一个任务；排队或执行中超过 10 分钟没有结果显示 `unknown` |
| `POST /agents/deployments` | 新部署必须带 `model: {connection_id, model_id}`：选择即授权给新实例，实例、连接授权、绑定、应用任务同一事务 |

实例接口（`/agent-api/v1/*`，实例凭证；响应 `Cache-Control: no-store`）：

| 方法与路径 | 说明 |
|---|---|
| `GET /model-config` | 自己的非敏感配置：provider 键（`mwc` + 连接 ID 前 20 个十六进制字符）、OpenClaw `api`、base URL、模型 ID、SecretRef id（`mc_<连接 ID>`）、`apply_key`、`fingerprint`；`state=revoked` 表示移除受管配置。没有绑定或连接不可用 `409 MODEL_CONFIGURATION_INCOMPLETE`（`details.reason`）。每分钟 6 次 |
| `POST /model-config/applied` | 回报 `applied` / `failed` / `manual_required`（`reason` 是固定的小写代码）；只认当前目标的 `desired_revision`，迟到的旧回报 `409 MODEL_BINDING_CONFLICT`、不改状态 |
| `POST /model-credentials/resolve` | 取 `mc_` Key：只给当前目标或尚未退休的已应用连接，且连接启用、仍授权、绑定没有解除；其他一律 `errors[id].code = NOT_FOUND`；每次取用都写记录。每分钟 60 次 |

密钥库 `/api/v1/agents/secrets*` 与 `/agent-api/v1/secrets/resolve` 只管非模型凭据（GitHub Token、运行环境注入的密钥等，
页面在中转站管理的“兼容凭据”页签）：连接自己的 Key 不出现在密钥库里（列表、详情、修改、授权都当作不存在），
新建 `kind=model_api_key` 返回 `422 VALIDATION_FAILED`（`details.field = kind`）。旧版兼容（导入、迁移记录、旧配置状态）已删除（T-0209）。

## 大模型客户端（会话桥）模块 `llm_bridge`（T-0228、T-0230；docs/v2/LLM_BRIDGE_PLAN.md）

页面叫“大模型客户端管理”；页面上的一个 MCP 管理器就是这里的一座会话桥（`bridge`）。

模块停用时下面的接口一律 `404 MODULE_DISABLED`。查看要 admin，修改要 owner（带 CSRF）。
登录、环境、设置、会话是转给会话桥管理接口的结果；会话桥连不上时 `503 EXTERNAL_UNAVAILABLE`，令牌不对时 `503 BRIDGE_AUTH_FAILED`。

| 接口 | 说明 |
|---|---|
| `GET /api/v1/llm_bridge/bridges` | 登记的会话桥；令牌不回传，只有 `has_token` |
| `POST /api/v1/llm_bridge/bridges` | 登记（添加一个 MCP 管理器）：`name`、`base_url`（http / https）、`token`（≥ 32 个字符，加密保存）、可选 `platforms`（它控制的平台，至少一个）。填了 `platforms` 就先写进会话桥，同时验证地址和令牌：`503 EXTERNAL_UNAVAILABLE` / `503 BRIDGE_AUTH_FAILED` / `409 BRIDGE_OUTDATED`（会话桥版本太旧）时不登记。`409 NAME_TAKEN` |
| `PATCH` / `DELETE /api/v1/llm_bridge/bridges/{id}` | 改名字、地址、令牌、启用停用；删除登记（不影响会话桥容器；各实例权限的 `bridge_ids` 里去掉它，只允许用它的实例之后一个都不能用） |
| `GET /api/v1/llm_bridge/bridges/{id}/status` | 各账号槽位（`openai` / `claude` / `workbuddy`）的登录状态、通道、排队数、设置；连不上时 `reachable=false` 加 `error`，不报错。账号的 `identity` 是打码后的账号标识（用密钥或令牌登录时为空），`registered` 表示这座桥登记过这个平台的账号（退出登录后仍为 true，“更换账号”后才清掉），`method` 只在已登录时有值 |
| `PUT /api/v1/llm_bridge/bridges/{id}/settings` | `platforms`（控制的平台：`chatgpt` / `codex` / `claude` / `workbuddy`，至少一个）、`concurrency`（1 ~ 16）、`turn_timeout_seconds`（30 ~ 21600）、`send_per_hour`、`max_text_chars`、`claude_subscription_ack`。没勾选的平台不能登录（`409 CONFLICT`）、不能新建会话和发送；已有的登录和会话记录保留 |
| `POST /api/v1/llm_bridge/bridges/{id}/accounts/{slot}/login` | `method`：`device_code`（返回 `verification_url`、`user_code`，`state=pending`）/ `api_key` / `oauth_token` / `existing`。Claude 的非 `api_key` 方式在确认风险前 `403 FORBIDDEN` |
| `GET .../accounts/{slot}/login` | 登录进行到哪一步：`pending` / `done` / `failed` / `none` |
| `POST .../accounts/{slot}/refresh` / `logout` / `reset` | 重新检查状态；退出登录；更换账号（归档这个槽位的全部会话，返回 `archived_sessions`） |
| `GET` / `POST /api/v1/llm_bridge/bridges/{id}/environments`，`PUT` / `DELETE .../environments/{env_id}` | 开发环境：`platform`、`name`、`repo_url`（只收 https）、`branch`、`sandbox`（`read_only` / `workspace_write` / `exec`）、`network`、`description`、`enabled` |
| `GET /api/v1/llm_bridge/bridges/{id}/sessions?limit&offset&sync` | 会话桥上的会话；`sync=true` 先登记官方程序里有、桥里没有的会话 |
| `GET .../sessions/{session_id}/turns` | 一个会话的回合（最新在前）：消息、状态、结果或失败原因 |
| `GET /api/v1/llm_bridge/grants`，`PUT /api/v1/llm_bridge/grants/{instance_id}` | 每个实例的权限：`enabled`、`can_write`、`can_account`、`can_takeover`、`platforms`（空 = 全部）、`bridge_ids`（允许用的 MCP 管理器：`null` = 全部，包括以后添加的；空列表 = 一个都不能用；填了不存在的 id 时 `422`）。`enabled=false` 时三项权限一并收回，`platforms` 与 `bridge_ids` 保留 |

状态里的 `platforms` 是这座桥控制的平台；每个账号和通道带 `enabled`（这座桥是否控制它对应的平台），没勾选的通道 `available=false`。

**聊天页面**（T-0231，`docs/v2/LLM_CHAT_PLAN.md`；都在 `/api/v1/llm_bridge/bridges/{id}/chat` 下，owner / admin 可用，写操作带 CSRF）。
人以 `user:<用户 id>` 的身份经这座桥发消息，和智能体走同一套工具（密钥拦截、限额、账号一致性、排队都一样）；审计只记动作，不记消息内容。

| 接口 | 说明 |
|---|---|
| `GET .../chat/sessions?q&platform&status&limit&offset&sync` | 这座桥上的会话（自己的、别的管理员的、智能体的、官方程序里已有的）：`q` 按标题搜索，`status` 是 `active`（默认）/ `archived` / `all`；每个会话带 `owner`（显示名）、`owner_kind`（`me` / `user` / `agent` / `external`）、`busy`（有回合没做完）；返回 `next_offset`、`sync_failed` |
| `GET .../chat/sessions/{session_id}` | 会话信息（开发会话带 `environment`）和消息：`user` 消息带 `by`、`mine`；`assistant` 消息带所在回合的 `status`（`queued` / `running` / `completed` / `failed` / `cancelled` / `unknown`）、`steps`（过程：`message` / `command` / `files` / `notice`）、`failure`、`ahead_in_session`；没做完的回合给出已经得到的部分；`last_event_id` 用来接着读事件 |
| `GET .../chat/sessions/{session_id}/events?after&wait_seconds` | `after` 之后的事件（`queued` / `started` / `message` / `command` / `files` / `notice` / `completed` / `failed` / `cancelled` / `unknown`）；没有新事件且还有回合没做完时最多等 `wait_seconds`（0 ~ 25）秒；返回 `busy`、`last_id` |
| `GET .../chat/options?platform&mode` | 新建会话时可选的模型（带 `default`、`efforts`）和开发环境；读不到模型时 `models` 为空、`models_error` 说明原因 |
| `POST .../chat/sessions` | 新建会话：`platform`、`mode`（`chat` / `dev`）、可选 `model`、`effort`、`environment`（开发会话必填）、`title`，可以带第一条消息 `text` + `request_id`；`201` 返回 `session`、`turn`；会话建好但第一条没发出去时 `send_error` 说明原因 |
| `POST .../chat/sessions/{session_id}/messages` | 发一条消息：`text`、`request_id`（幂等号，重试用同一个不会发两次）、`takeover`；立刻返回回合，不等回答。不是自己建的会话要 `takeover=true`，否则 `409 TAKEOVER_REQUIRED` |
| `POST .../chat/turns/{turn_id}/cancel` | 停止排队中或进行中的回合 |
| `PATCH .../chat/sessions/{session_id}` | 改标题 |
| `POST .../chat/sessions/{session_id}/archive` | 归档（之后不能再发送） |

写操作在这座桥停用时 `409 BRIDGE_DISABLED`；桥没有及时回应、结果未知时 `504 EXTERNAL_TIMEOUT`；会话桥自己的拒绝（未登录、限额、密钥拦截等）按原样带着说明返回。

**多模型同问**（T-0232；都在 `/api/v1/llm_bridge/chat/groups` 下，owner / admin 可用，写操作带 CSRF）。一组同问可以跨 MCP 管理器，分组登记在工作台；
每个成员是某座桥上的一个普通会话，内容和进度用上面的单会话接口读。

| 接口 | 说明 |
|---|---|
| `GET .../chat/groups?status` | 各组同问（按最后活动排序）：`title`、`status`（`active` / `archived`）、`owner`、`owner_kind`（`me` / `user`）、`members`；`status` 默认 `active` |
| `POST .../chat/groups` | 把同一个问题同时发给几个模型：`text`、`request_id`（同一个人用同一个再建一次，得到的是同一组）、可选 `title`、`targets`（2 ~ 6 个：`bridge_id`、`platform`、`mode`、可选 `model`、`effort`、`environment`）。`201` 返回 `group` 和每个目标的 `results`（`turn` 或 `error`）。某个目标失败只记在它自己的成员里；一个会话都没建成时 `409`；管理器停用时 `409 BRIDGE_DISABLED` |
| `GET .../chat/groups/{group_id}` | 一组和它的成员：`position`、`bridge_id`（登记被删除后为空）、`bridge_name`、`bridge_enabled`、`platform`、`mode`、`model`、`session_id`（没建成时为空）、`error` |
| `POST .../chat/groups/{group_id}/messages` | 向每个成员继续发：`text`、`request_id`、`takeover`；返回每个成员的 `turn` 或 `error`（管理器停用、登记被删除、会话没建成的这次不发）。别人建的一组要 `takeover=true`，否则 `409 TAKEOVER_REQUIRED`；已归档的一组 `409` |
| `POST .../chat/groups/{group_id}/archive` | 归档这一组：只是不再并排显示，每个模型的会话不受影响 |

**智能体工具**（只列给打开了权限的实例；每个工具都有可选参数 `bridge`，实例能用多个 MCP 管理器时必填，`llm_bridge_status` 除外：不填时返回 `data.bridges`，列出它能用的每一个和各自控制的平台、登录情况；连不上的标 `reachable=false`）：

| 工具 | 风险 | 说明 |
|---|---|---|
| `llm_bridge_status` / `llm_bridge_models` / `llm_bridge_environments` | read | 平台与登录状态；可选模型；可选环境 |
| `llm_bridge_session_list` / `llm_bridge_session_get` / `llm_bridge_turn_get` | read | 会话列表（`scope=account` 需要 `can_account`）；会话内容（别人的会话需要 `can_takeover`）；回合状态与结果 |
| `llm_bridge_session_create` / `llm_bridge_send` / `llm_bridge_turn_cancel` / `llm_bridge_session_archive` | direct | 需要 `can_write`；直接执行、不审批，记执行记录；新建会话通知管理员（`llm_bridge.session_created`）。依据是 `docs/AGENTS.md` 的例外九（人类 2026-11-13 批准） |

结果统一是 `{bridge, data}`，`data` 是会话桥返回的内容。回合的 `data.status`：`queued` / `running` / `completed`（带 `result`、`summary`）/
`failed` / `unknown`（带 `failure.kind`、`failure.message`）/ `cancelled`。错误码：`FORBIDDEN_CAPABILITY`（没有授权、平台不在允许范围、管理员没有给这个实例开任何 MCP 管理器）、
`NOT_FOUND`（包括 `bridge` 填了没有授权的管理器）、`VALIDATION_FAILED`、`CONFLICT`（会话已归档、`request_id` 用在了别的会话）、`RATE_LIMITED`、`NOT_SUPPORTED`（接口桩通道、这座桥没有控制这个平台）、
`EXTERNAL_UNAVAILABLE`（会话桥连不上、平台未登录、账号不一致）。
