# Sprint 1 验收报告

- 验收人：claude（协作任务 T-0013，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30
- 结论：**Sprint 1 的 DoD 全部通过**；“未运行”项不属于 Sprint 1 范围或受环境限制，已逐条说明。
- 是否进入 Sprint 2：等待人类批准（D-0003：是否进入下一个 Sprint 由人类决定）。

## 1. Sprint 1 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 不登录访问私有接口 401 | 通过 | 全栈脚本 `tests/e2e/sprint1_acceptance.py` 经 Caddy 请求 `/me`、`/workspaces`、`/workspaces/{id}`、`/auth/sessions`、`/audit-logs`：均 `401 UNAUTHORIZED`，响应不含用户邮箱与工作空间名称；伪造会话 Cookie 也是 401。pytest：`test_private_endpoints_require_a_session` |
| 普通用户不能跨工作空间访问 | 通过 | 全栈脚本：第二个用户读取 / 修改 owner 的工作空间、读取 owner 的审计日志，owner 读取对方工作空间，读取不存在的工作空间——均返回相同的 `404 NOT_FOUND`，owner 的数据未被改动；加入为 viewer 后可查看、修改与查看审计日志均为 `403 FORBIDDEN`。pytest：两用户两工作空间的越权测试与 viewer / member / admin 角色矩阵 |
| 登出后 session 失效 | 通过 | 全栈脚本：登出 204 后用旧 Cookie 访问 `/me` 返回 401；另一台设备的会话被撤销后立即 401。浏览器：撤销另一台设备的会话后，该设备访问 `/settings` 被引导登录。pytest：登出、撤销会话、修改密码撤销其他会话，以及与改密并发的旧密码登录拿不到会话 |
| 迁移可从空库完成 | 通过 | CI `ci-infra`：在全新 runner 的空数据卷上 `make migrate`（0001 → 0002 → 0003）；全栈脚本确认 `alembic current` 为 head；pytest：空库 upgrade → `alembic check` → downgrade → upgrade |

## 2. 验收场景（规范第 15 节）与 Sprint 1 关键路径

全栈环境：Compose 6 个服务（全新数据卷），经 Caddy 对外入口访问。接口级场景由 `tests/e2e/sprint1_acceptance.py` 自动化，**已纳入 CI**（`ci-infra.yml` 的 “Sprint 1 全栈验收” 步骤，在 `make smoke` 之后、任何创建用户的步骤之前运行）。

| 场景 | 期望 | 结果 |
|---|---|---|
| A01 首次部署空数据库 | 创建管理员与工作空间；迁移无错误 | 通过：空库时 `setup_required: true`；错误安装凭据 403；首次安装 201，创建 owner 与默认工作空间，会话 Cookie 为 HttpOnly + SameSite=Lax；再次安装 409 |
| A02 未登录调用私有 API | 返回 401，不泄露内容 | 通过：见第 1 节。任务 API 属于 Sprint 3，本 Sprint 以全部私有接口验证 |
| A18 登录后 logout | 旧 Session 不能再访问私有 API | 通过 |
| 跨工作空间访问（S1-03） | 非成员 404、角色不够 403 | 通过 |
| CSRF 与来源校验（S1-02） | 缺少 Token 或来源不可信被拒绝 | 通过：缺少 `X-CSRF-Token` 的写请求、`Origin: https://evil.example` 的登录均为 `403 CSRF_FAILED` |
| 审计日志（S1-05） | 记录安全事件 | 通过：`setup.completed`、`auth.login_succeeded`、`auth.session_revoked`、`auth.logout` 均出现在 owner 的审计日志中 |

全栈脚本共 32 项检查全部通过；在非空库上运行时会明确失败并返回退出码 1，不会误报。

### 浏览器走通（`tests/e2e/sprint1_browser.cjs`，无头 Chromium，Playwright 1.63.0）

| 步骤 | 结果 |
|---|---|
| 空库时打开首页 → 首次安装页；填写安装凭据、显示名、工作空间、邮箱、密码 → 进入首页，顶栏显示管理员 | 通过 |
| 登出后访问 `/settings` → 登录页（带 `redirect`）→ 登录后回到 `/settings` | 通过 |
| 修改显示名与工作空间名称 | 通过 |
| 另一台设备登录 → 在安全设置里撤销它的会话（确认对话框）→ 列表只剩当前会话；该设备被引导重新登录 | 通过 |
| 审计日志页显示撤销会话、修改设置、首次安装等记录 | 通过 |
| 登出后访问受保护页面 → 登录页 | 通过 |

页面无脚本错误（登录前 / 登出后查询 `/me` 的 401 是预期响应）。运行方式：`cd tests/e2e && npm ci && npm run browser:install`，再 `node sprint1_browser.cjs <BASE_URL>`（Playwright 版本由该目录的锁文件固定）；暂未纳入 CI（见第 5 节）。

## 3. 任务清单（docs/TASKS.md Sprint 1）

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S1-01 User、Session、Workspace、Member 迁移及 repository | T-0010（claude，PR #13） | 迁移 0002；`alembic check` 无差异；空库 upgrade / downgrade 测试 |
| S1-02 首次安装、Argon2id、Session Cookie、CSRF 与限流 | T-0010（PR #13），前端 T-0012（codex，PR #16） | Argon2id（t=3、m=64 MiB、p=4）；安装凭据只能用一次；HttpOnly / SameSite=Lax Cookie；CSRF Token + 来源校验；账户 + IP 双维度登录限流（集成测试与 T-0010 经 Caddy 实测 429 + `Retry-After`） |
| S1-03 Workspace 与 role policy，防 IDOR 用例 | T-0011（claude，PR #14），前端 T-0012 | 统一 Policy：非成员 404、角色不够 403；两用户两工作空间测试；全栈脚本复验 |
| S1-04 Settings 骨架、连接凭证密文或 secret 引用 | T-0011（PR #14），前端 T-0012 | 个人资料与工作空间设置（含前端只读态）；`app/domain/credentials.py`：AES-256-GCM（绑定用途）与 `env:WS_SECRET_*` 引用，单元测试覆盖加解密、篡改与密钥错误 |
| S1-05 Audit logs、会话撤销与 API 测试 | T-0011（PR #14），前端 T-0012 | 迁移 0003（触发器拒绝 UPDATE、metadata 脱敏、IP 带密钥哈希）；会话列表与撤销；修改密码撤销其他会话；审计游标分页 |

## 4. 交付完成定义（docs/DEVELOPMENT_SPEC.md 第 18 节）

| 条件 | 结果 | 说明 |
|---|---|---|
| 代码已提交到版本控制 | 通过 | PR #12（契约）、#13、#14、#16 与本 PR |
| 所有必需依赖固定版本 | 通过 | `uv.lock`、`package-lock.json`；新增依赖（argon2-cffi、cryptography、openapi-fetch、vee-validate 等）的版本与核验记录在后端 / 前端技术栈 ADR |
| 开发和部署命令可复现 | 通过 | `make env / up / migrate / smoke`；`make setup-token` 查看安装凭据；验收脚本在本机与 CI 共用 |
| 迁移可执行 | 通过 | 本机测试库、CI service container、Compose 容器内三处执行，均到 0003 |
| 自动测试通过 | 通过 | 后端 `71 passed`（含真实 PostgreSQL 16 / Redis 7 集成测试）；前端 `Tests 50 passed`；CI：ci-api、Web CI（含生成类型一致性检查）、ci-infra（含全栈验收） |
| 手工 Smoke Test 通过 | 通过 | `make smoke` 9 项；浏览器走通第 2 节流程 |
| 关键安全测试完成 | 通过（Sprint 1 范围） | 未授权访问、IDOR / 越权、CSRF 与来源校验、登录与改密限流、会话撤销、改密与登录并发、审计只追加与脱敏、凭证加密的篡改与错误密钥。SSRF、Webhook、AI 工具策略随 Sprint 3、6 实现 |
| README / 配置示例更新 | 通过 | README、`docs/deployment.md`（安装凭据、可信来源、登录锁定解除、凭证密钥保管）、`.env.example`、`apps/web/README.md` |
| 未实现能力明确标注 | 通过 | 其他业务页面标注“规划中”；凭证加密服务尚无使用方（Sprint 3）；OpenAPI 中残留的 501 声明见契约 ADR 第 1 节 |
| 无明文密钥 | 通过 | 仓库只有占位符与测试常量；`.env` 被 git 忽略 |
| 无未经验证的第三方 API 假设 | 通过 | Sprint 1 不连接第三方服务；新增库的版本均在 PyPI / npm registry 核验 |

## 5. 未运行 / 限制

| 项目 | 原因 |
|---|---|
| 浏览器流程纳入 CI | 需要在 CI 安装 Playwright 与 Chromium；本 Sprint 先以本机运行结果作为证据，接口级场景已在 CI 自动化。是否把浏览器测试加入 CI，建议 Sprint 2（规范 S2-05 要求 E2E）一并决定 |
| 全栈环境下的登录限流 | 会锁定验收账户，未放进全栈脚本；由集成测试覆盖，T-0010 时已经 Caddy 实测（第 6 次错误登录 429，`Retry-After` 897，Redis 计数键使用真实客户端 IP） |
| HTTPS 与 `Secure` Cookie | 开发环境只用 HTTP；`APP_ENV=production` 或 https 地址下加 `Secure` 由单元测试覆盖，生产启用步骤见 `docs/deployment.md` 第 6 节 |
| 真实移动设备与读屏 | 只有 jsdom 测试、桌面无头浏览器，以及 codex 在 390px 视口（API mock）的检查 |
| 备份与恢复演练 | 规范安排在 S6-04 |
| 本地 `docker build` 的网络 | 验收环境出网需经代理：本地构建时额外传入代理与 CA（只在构建阶段，不进入运行镜像）；仓库 Dockerfile 未改，CI 按原样构建 |

## 6. 遗留问题与建议（不影响 Sprint 1 验收）

1. **契约生成文件的提交方（需要人类决定）**：T-0012 引入 `npm run check:api`，后端修改 `services/api/openapi.json` 的 PR 必须同时提交重新生成的 `apps/web/src/services/schema.d.ts`，否则 Web CI 失败；而该文件位于 codex 负责的 `apps/web`。建议：把 `schema.d.ts` 视为契约的生成产物，修改契约的一方运行 `npm run generate:api` 并在同一 PR 提交（只允许生成，不允许手改）。这属于协作规则，请人类确认或另定。
2. **OpenAPI 中的 501 声明**：Sprint 1 暂时保留，Sprint 2 第一次修改契约时一并清理（契约 ADR 第 1 节）。
3. **审计日志的操作者显示**：前端目前显示用户 ID；后续有成员接口后可映射为显示名（PR #16 审查意见）。
4. **GitHub 上 `@codex` 派活**：Codex 连接器提示该仓库缺少云端环境，目前通过 Codex 应用的云端聊天派发任务（issue #15、PR #16 即此方式）。属于协作工具配置，不影响产品。

## 7. 验收时执行的命令（摘录）

```
# 全新数据卷的独立 Compose 项目（避免影响已有数据），端口 18080
$ COMPOSE_PROJECT_NAME=ws-accept HTTP_PORT=18080 CORS_ALLOWED_ORIGINS=http://127.0.0.1:18080 docker compose up -d --wait
$ make migrate
Running upgrade  -> 0001 / 0001 -> 0002 / 0002 -> 0003

$ python3 tests/e2e/sprint1_acceptance.py --base-url http://127.0.0.1:18080
通过  迁移在最新版本（alembic current 为 head）（0003 (head)）
通过  A01 空库时需要首次安装
…（共 32 项）
通过  A18 登出后旧会话不能再访问私有接口 → 401（401）
通过  审计日志记录了安装、登录、撤销会话与登出
全部通过

# 重置为空库后
$ cd tests/e2e && npm ci && node sprint1_browser.cjs http://127.0.0.1:18080
通过  空库时打开首页 → 首次安装页
…
全部通过（页面无错误）

$ make lint
All checks passed! / 63 files already formatted / Success: no issues found in 63 source files / eslint、vue-tsc 通过
$ make test        # TEST_DATABASE_URL / TEST_REDIS_URL 指向真实 PostgreSQL 16 / Redis 7
71 passed
Tests  50 passed (50)
$ make build-web
✓ built
$ (cd apps/web && npm run check:api)       # 生成类型与 openapi.json 一致
$ (cd services/api && uv run python -m scripts.export_openapi --check)
openapi.json 是最新的
```

main 上 PR #16 合并提交 `87cdc16` 的 ci-api、Web CI、ci-infra、collab、collab-sync 全部成功。
