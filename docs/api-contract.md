# API 契约

> 状态：Sprint 1 契约已声明。完整规范见 `docs/DEVELOPMENT_SPEC.md` 第 6 节。
> **机器可读的接口以 `services/api/openapi.json` 为准**（`make openapi` 由代码生成，CI 检查一致性），前端据此生成类型。
> Sprint 1 的 Cookie、CSRF、工作空间上下文、错误码等约定见 `docs/decisions/2026-10-30-sprint-1-auth-contract.md`。

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
| PATCH | `/api/v1/me` | 修改显示名 | 501，T-0011 实现 |
| GET | `/api/v1/workspaces` | 我所属的工作空间 | 已实现（T-0010） |
| GET / PATCH | `/api/v1/workspaces/{workspace_id}` | 工作空间详情 / 设置 | 501，T-0011 实现 |
| GET | `/api/v1/auth/sessions` | 我的会话 | 501，T-0011 实现 |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 撤销会话 | 501，T-0011 实现 |
| POST | `/api/v1/auth/password` | 修改密码 | 501，T-0011 实现 |
| GET | `/api/v1/audit-logs` | 审计日志（需 `X-Workspace-Id`） | 501，T-0011 实现 |

请求头约定：写请求带 `X-CSRF-Token`（值取自 `ws_csrf` Cookie）；工作空间范围的请求带 `X-Workspace-Id`。
