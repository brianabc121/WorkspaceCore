# ADR：Sprint 1 接口契约——认证、会话、工作空间、设置与审计

- 状态：已采纳
- 日期：2026-10-30
- 作者：claude
- 相关：协作任务 T-0009（本契约）、T-0010 / T-0011（后端实现，claude）、T-0012（前端，codex）；
  `docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；D-0003 第 2 条（契约先行）

## 1. 背景与原则

Sprint 1 由 claude 做后端、codex 做前端，两边并行。本契约先固定接口：

- **机器可读的契约是 `services/api/openapi.json`**，由后端代码生成（`make openapi`），CI 检查它与代码一致。
  前端用它生成 TypeScript 类型，不手抄字段。本文说明 OpenAPI 表达不了的约定（Cookie、CSRF、错误码含义等）。
- 端点已全部声明。还没实现的端点返回 `501 NOT_IMPLEMENTED`（统一错误格式），由 T-0010 / T-0011 逐步实现；
  实现过程中如需修改契约，必须同时更新 `openapi.json` 和本文，并在 PR 里写明。

## 2. 端点总览

| 方法 | 路径 | 登录 | CSRF | 角色 / 范围 | 成功 | 实现 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/setup/status` | 否 | — | 公开 | 200 `SetupStatus` | T-0010 |
| POST | `/api/v1/setup` | 否 | 来源校验 | 仅在没有任何用户时 | 201 `MeResponse` + 设置 Cookie | T-0010 |
| POST | `/api/v1/auth/login` | 否 | 来源校验 | — | 200 `MeResponse` + 设置 Cookie | T-0010 |
| POST | `/api/v1/auth/logout` | 是 | 是 | 本人 | 204，清除 Cookie | T-0010 |
| GET | `/api/v1/me` | 是 | — | 本人 | 200 `MeResponse` | T-0010 |
| PATCH | `/api/v1/me` | 是 | 是 | 本人 | 200 `UserOut` | T-0011 |
| GET | `/api/v1/workspaces` | 是 | — | 本人所属 | 200 `Page[WorkspaceOut]` | T-0010 |
| GET | `/api/v1/workspaces/{workspace_id}` | 是 | — | viewer 及以上 | 200 `WorkspaceOut` | T-0011 |
| PATCH | `/api/v1/workspaces/{workspace_id}` | 是 | 是 | admin 及以上 | 200 `WorkspaceOut` | T-0011 |
| GET | `/api/v1/auth/sessions` | 是 | — | 本人 | 200 `Page[SessionOut]` | T-0011 |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 是 | 是 | 本人的会话 | 204 | T-0011 |
| POST | `/api/v1/auth/password` | 是 | 是 | 本人 | 204 + 重新签发 Cookie | T-0011 |
| GET | `/api/v1/audit-logs` | 是 | — | owner / admin（`X-Workspace-Id`） | 200 `Page[AuditLogOut]` | T-0011 |

`/healthz`、`/readyz` 不变（Sprint 0）。

## 3. 会话 Cookie

| 项目 | 约定 |
|---|---|
| 名称 | `ws_session` |
| 值 | 32 字节随机数（base64url）；数据库只存 SHA-256 哈希（`auth_sessions.token_hash`） |
| 属性 | `HttpOnly; SameSite=Lax; Path=/`；`APP_ENV=production` 时加 `Secure` |
| 有效期 | 绝对有效期 30 天（`expires_at`）；7 天没有任何请求即失效（按 `last_seen_at` 判断） |
| 签发 | 首次安装成功、登录成功、修改密码成功时签发新会话 |
| 撤销 | 登出撤销当前会话；修改密码撤销该用户全部会话后为当前客户端签发新会话；可单独撤销自己的任一会话 |

浏览器同源请求会自动携带 Cookie：前端 `fetch` 使用 `credentials: "same-origin"`（默认值即可），不读取、不保存会话 Token。

## 4. CSRF 防护

1. 签发会话时同时设置 `ws_csrf` Cookie：值为 `HMAC-SHA256(SESSION_SECRET, 会话 Token)` 的 base64url，
   `SameSite=Lax; Path=/`，**不是** HttpOnly（前端需要读取），生产环境加 `Secure`。
2. 前端对所有 `POST / PUT / PATCH / DELETE` 请求，读取 `ws_csrf` Cookie，放进请求头 `X-CSRF-Token`。
3. 后端校验：
   - 需要登录的写请求：`X-CSRF-Token` 必须与按当前会话重新计算的值一致（常量时间比较），否则 `403 CSRF_FAILED`；
   - 所有写请求（含登录、首次安装）：请求带 `Origin`（或只带 `Referer`）时，其来源必须在可信列表中，否则 `403 CSRF_FAILED`。
     可信来源 = `PUBLIC_BASE_URL` + `CORS_ALLOWED_ORIGINS`（逗号分隔）。
   - 登录、首次安装只做来源校验、不校验 Token：此时还没有可用的会话；而浏览器里残留的过期会话 Cookie，
     或轮换 `SESSION_SECRET` 后失效的旧 Token，都不应该让用户无法重新登录（T-0010 实现时确认）。
4. 本机开发时浏览器访问 `http://localhost:5173`（Vite 代理到 API），需要把它加入 `.env` 的 `CORS_ALLOWED_ORIGINS`。

## 5. 工作空间上下文

- **工作空间本身**用路径参数：`/api/v1/workspaces/{workspace_id}`。
- **工作空间内的资源**（本 Sprint 只有审计日志；之后的仪表盘、任务等同理）沿用规范第 6.1 节的路径，
  用请求头 **`X-Workspace-Id: <uuid>`** 指定当前工作空间。缺少该头返回 `422 VALIDATION_FAILED`。
- 权限判断在服务端 Policy 层统一进行（规范第 9.2 节）：
  - 不是该工作空间成员：`404 NOT_FOUND`（不暴露工作空间是否存在，防止 IDOR 枚举）；
  - 是成员但角色不够：`403 FORBIDDEN`。
- 前端在 Pinia 中保存当前工作空间 ID（默认 `GET /me` 返回的第一个），对工作空间范围的请求带上该头。

## 6. 角色

`owner > admin > member > viewer`。本 Sprint 涉及的权限：

| 操作 | owner | admin | member | viewer |
|---|---|---|---|---|
| 查看工作空间 | ✓ | ✓ | ✓ | ✓ |
| 修改工作空间设置 | ✓ | ✓ | — | — |
| 查看审计日志 | ✓ | ✓ | — | — |

个人资料、会话、密码只能操作自己的。首次安装创建的用户是默认工作空间的 owner。

## 7. 错误码

统一错误格式见规范第 6 节（`{"error": {"code", "message", "request_id", "details"}}`）。

| HTTP | code | 场景 |
|---|---|---|
| 400 | `INVALID_CURRENT_PASSWORD` | 修改密码时当前密码错误 |
| 401 | `UNAUTHORIZED` | 没有会话、会话过期或已撤销 |
| 401 | `INVALID_CREDENTIALS` | 登录失败；账户不存在、密码错误、账户停用都返回这一个，不区分 |
| 403 | `CSRF_FAILED` | CSRF Token 缺失 / 不匹配，或请求来源不可信 |
| 403 | `FORBIDDEN` | 角色不够 |
| 403 | `SETUP_TOKEN_INVALID` | 首次安装凭据错误或未配置 |
| 404 | `NOT_FOUND` | 资源不存在，或不属于你能访问的工作空间 |
| 409 | `SETUP_COMPLETED` | 已经存在用户，不能再次首次安装 |
| 422 | `VALIDATION_FAILED` | 参数校验失败；`details.errors` 只含位置与原因，不回显输入 |
| 429 | `RATE_LIMITED` | 限流；响应头 `Retry-After` 给出秒数 |
| 501 | `NOT_IMPLEMENTED` | 契约已声明、尚未实现 |

前端处理建议：`401` 跳转登录页（若 `setup/status` 显示需要安装，则跳转安装页）；`403 CSRF_FAILED` 提示刷新后重试；`429` 按 `Retry-After` 提示。

## 8. 首次安装

- 只在数据库里还没有任何用户时可用；之后返回 `409 SETUP_COMPLETED`，`setup/status` 返回 `setup_required: false`。
- 请求必须携带部署时生成的一次性凭据 `setup_token`，与环境变量 `SETUP_TOKEN` 做常量时间比较。
  `make env` 会随机生成它（T-0010 实现，并提供查看方式）。这样即使实例暴露在网络上，陌生人也无法抢先注册成管理员；
  规范第 9.1 节要求不提供硬编码的默认管理员密码。
- 成功时在一个事务里创建 owner 用户、默认工作空间（名称来自请求）和成员关系，签发会话并返回 `201 MeResponse`。
- 按 IP 限流，规则同登录。

## 9. 登录、密码与限流

- 邮箱不区分大小写（服务端统一转小写保存与比较）。
- 密码使用 Argon2id 哈希（`argon2-cffi`，参数由 T-0010 写入 backend-stack ADR）；新密码 12–128 个字符。
- 账户不存在时也对一个固定的虚拟哈希做一次校验，使响应时间与密码错误时相同，避免据此判断账户是否存在。
- 登录失败限流（Redis 固定窗口计数，T-0010 已实现）：

  | 维度 | 默认阈值 | 环境变量 |
  |---|---|---|
  | 同一账户 | 15 分钟内失败 5 次 | `LOGIN_MAX_FAILURES_PER_ACCOUNT` |
  | 同一 IP | 15 分钟内失败 10 次 | `LOGIN_MAX_FAILURES_PER_IP` |
  | 窗口 | 900 秒 | `LOGIN_FAILURE_WINDOW_SECONDS` |
  | 每次失败的额外延迟 | 0.5 秒 | `LOGIN_FAILURE_DELAY_SECONDS` |

  达到阈值后返回 `429 RATE_LIMITED`（锁定期间密码正确也拒绝），成功登录会清除该账户的计数。
  账户维度的键用邮箱的 SHA-256，不在 Redis 中保存明文邮箱。首次安装按 IP 使用同样的阈值。
- 客户端 IP：Compose 中 API 只能经 Caddy 访问，设置 `TRUST_FORWARDED_FOR=true`，取 `X-Forwarded-For` 最右侧的地址；
  直接对外暴露 API 时必须关闭该选项，否则客户端可以伪造 IP 绕过限流。

## 10. 前端约定（T-0012）

- 用 `openapi-typescript`（或同类工具）从 `services/api/openapi.json` 生成类型；后端修改接口后重新生成。
- 所有请求走相对路径 `/api/v1/...`；写请求自动带 `X-CSRF-Token`；工作空间范围的请求自动带 `X-Workspace-Id`。
- 启动时先请求 `GET /api/v1/setup/status`：需要安装 → 安装页；否则请求 `GET /api/v1/me`：`401` → 登录页。
- 登录、安装、修改密码成功后，新 Cookie 由浏览器自动保存，前端无需处理 Token。
- 后端未实现的端点返回 501：开发阶段用 mock（例如 MSW）对接，联调在 T-0013 进行。

## 11. 后果

- 前端可以立即基于 `openapi.json` 开发；后端按端点逐个替换 501。
- `X-Workspace-Id` 头会成为之后所有工作空间资源接口的统一做法。
- CSRF 依赖 `SESSION_SECRET`：轮换该值会使现有 CSRF Token 全部失效（会话本身仍有效，前端需要重新登录或刷新 Cookie）。
