# API 契约

> 状态：Sprint 1 端点已全部实现（T-0010、T-0011）；Sprint 2 端点已实现（T-0015）。完整规范见 `docs/DEVELOPMENT_SPEC.md` 第 6 节。
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
| GET | `/api/v1/auth/sessions` | 我的会话 | 已实现（T-0011） |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 撤销会话 | 已实现（T-0011） |
| POST | `/api/v1/auth/password` | 修改密码 | 已实现（T-0011） |
| GET | `/api/v1/audit-logs` | 审计日志（需 `X-Workspace-Id`，owner / admin） | 已实现（T-0011） |

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

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET / POST | `/api/v1/integrations` | 集成列表（viewer）/ 新建 Vikunja 集成（admin，Token 只写不读） | 已声明，T-0020 实现 |
| PATCH | `/api/v1/integrations/{integration_id}` | 修改集成（admin，乐观并发） | 已声明，T-0020 实现 |
| POST | `/api/v1/integrations/{integration_id}/test` | 测试连接与权限（admin） | 已声明，T-0020 实现 |
| POST | `/api/v1/integrations/{integration_id}/sync` | 排队全量同步（admin，202） | 已声明，T-0021 实现 |
| GET | `/api/v1/projects` | 外部项目（投影，带 `sync` 新鲜度） | 已声明，T-0020 实现 |
| GET / POST | `/api/v1/tasks` | 任务列表（投影，过滤与分页）/ 创建任务（member，幂等键） | 已声明，T-0020 实现 |
| GET / PATCH / DELETE | `/api/v1/tasks/{task_id}` | 详情 / 修改 / 删除（member，幂等键 + `version`） | 已声明，T-0020 实现 |
| POST | `/api/v1/webhooks/vikunja/{integration_id}` | Vikunja 回调：只认签名，不用会话与 CSRF（202） | 已声明，T-0021 实现 |

新增错误码：`409 IDEMPOTENCY_KEY_REUSED` / `IDEMPOTENCY_IN_PROGRESS` / `OPERATION_NOT_APPLIED` / `INTEGRATION_UNAVAILABLE` / `SYNC_IN_PROGRESS`、
`401 WEBHOOK_SIGNATURE_INVALID`、`403 EXTERNAL_FORBIDDEN`、`413 PAYLOAD_TOO_LARGE`、`422 EXTERNAL_VALIDATION_FAILED`、
`502 INTEGRATION_UNAUTHORIZED`、`503 EXTERNAL_UNAVAILABLE` / `EXTERNAL_RATE_LIMITED`、`504 RESULT_UNKNOWN`（用同一个幂等键重发以查询结果）。
