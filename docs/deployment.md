# 部署与运行手册

> 状态：Sprint 1 进行中。core Compose 已可一键启动，首次安装与登录接口已实现（T-0010）；备份与恢复（`make backup` / `make restore-verify`）计划在 S6-04 实现，目前会明确报“尚未实现”。

## 1. 一键启动（推荐）

前提：Docker（含 Compose v2）、GNU Make、Python 3（只用来生成 `.env`）。

```bash
make up        # 首次会自动生成 .env（随机密码），构建镜像并等待所有服务健康
make migrate   # 首次启动后执行：把数据库迁移到最新版本
make smoke     # 经 Caddy 检查健康、就绪、前端页面与安全响应头
```

打开 http://localhost:8080 即可看到首页（这个地址就是 `.env` 里的 `PUBLIC_BASE_URL`，见第 3 节）。

### 首次安装

数据库里还没有任何用户时，需要先创建管理员（owner）和默认工作空间。为防止陌生人抢先注册，首次安装必须提供
部署时随机生成的**安装凭据**：

```bash
make setup-token   # 显示 .env 里的 SETUP_TOKEN
```

在网页的安装页面填写邮箱、显示名、密码（12–128 个字符）、工作空间名称和安装凭据即可（安装页由前端任务 T-0012 提供）。
在此之前可以直接调用接口：

```bash
curl -i http://localhost:8080/api/v1/setup -H 'Content-Type: application/json' -H 'Origin: http://localhost:8080' \
  -d '{"setup_token":"<安装凭据>","email":"me@example.com","display_name":"我","password":"<至少 12 个字符>","workspace_name":"我的工作台"}'
```

首次安装只能成功一次，之后返回 `409 SETUP_COMPLETED`。成功后可以把 `.env` 里的 `SETUP_TOKEN` 清空。

| 命令 | 作用 |
|---|---|
| `make env` | 从 `.env.example` 生成 `.env`，随机填写数据库密码、会话密钥等；`.env` 已存在则不改 |
| `make up` | 构建镜像并启动 6 个服务，等待全部健康检查通过（最多 300 秒） |
| `make migrate` | 在运行中的 `api` 容器里执行 `alembic upgrade head` |
| `make smoke` | 冒烟检查（`scripts/smoke.sh`），CI 使用同一个脚本 |
| `make ps` / `make logs` | 查看服务状态 / 跟踪日志 |
| `make down` | 停止并删除容器，**保留数据卷**（数据不会丢） |

## 2. 服务与网络

| 服务 | 镜像 | 作用 | 网络 | 对外端口 |
|---|---|---|---|---|
| caddy | `caddy:2.11.7-alpine` | 统一入口：路由、安全响应头、请求体限制 | frontend + integrations | `127.0.0.1:8080`（可配置） |
| web | `my-workspace-web:local`（`apps/web/Dockerfile`） | 前端静态文件（nginx，非 root） | frontend | 无 |
| api | `my-workspace-api:local`（`services/api/Dockerfile`） | FastAPI BFF（非 root） | backend + frontend + integrations | 无 |
| worker | 同 api 镜像 | ARQ 后台作业 | backend + integrations | 无 |
| postgres | `postgres:16.15-alpine3.24` | 数据库 | backend | 无 |
| redis | `redis:7.4.12-alpine3.21` | 缓存 / 作业队列 | backend | 无 |

- `backend` 是内部网络（不通外网）；数据库和 Redis 只在这个网络里，宿主机和外网都连不到。
- `integrations` 也是内部网络，只有 caddy、api、worker 加入，供独立部署的第三方服务（Vikunja 等，见第 8 节）与工作台互通；
  第三方服务碰不到 backend 里的数据库和 Redis。
- api、worker、web 以只读文件系统运行，去掉全部 Linux capabilities，禁止提权。
- Caddy 路由：`/api/*`、`/healthz` → api；`/readyz` 只接受内网来源（公网返回 404）；其余 → web。

数据卷：`postgres-data`、`redis-data`、`caddy-data`、`caddy-config`（Compose 项目名 `my-workspace`，卷名带此前缀）。

## 3. 环境变量（`.env`）

| 变量 | 说明 | 默认 |
|---|---|---|
| `APP_ENV` | `development` / `production`；production 关闭 API 文档页 | `development` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| `HTTP_BIND` / `HTTP_PORT` | Caddy 对外监听地址与端口 | `127.0.0.1` / `8080` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | 数据库；API / Worker 的连接串由这三项自动拼接 | `workspace` / `workspace` / 随机 |
| `DATABASE_URL` / `REDIS_URL` | **只在本机直接运行 API 时**使用，Compose 内不读取 | 指向 localhost |
| `PUBLIC_BASE_URL` | 浏览器访问工作台的地址；登录、安装等写请求的 `Origin` 必须是它或下面的可信来源 | `http://localhost:8080` |
| `CORS_ALLOWED_ORIGINS` | 其他可信来源，逗号分隔（例如用 IP 访问时的 `http://127.0.0.1:8080`） | `http://127.0.0.1:8080` |
| `SESSION_SECRET` | 计算 CSRF Token 的密钥；生产环境必填且至少 32 个字符。轮换后所有人需要重新登录 | 随机 |
| `SETUP_TOKEN` | 首次安装凭据（`make setup-token` 查看）；未配置时首次安装一律拒绝 | 随机 |
| `CREDENTIAL_ENCRYPTION_KEY` | 第三方连接凭证的加密密钥（64 位十六进制）；格式错误时 API 拒绝启动。**丢失或更换后已保存的凭证无法解密**，请与数据库备份分开保存 | 随机 |
| `INTEGRATION_ALLOWED_HOSTS` | 允许工作台访问的内网主机名，逗号分隔。第三方服务的地址解析到私有 / 内网 / 链路本地地址时一律拒绝（SSRF 防护），除非主机名在这里 | `vikunja` |
| `WEBHOOK_BASE_URL` | Vikunja 投递 Webhook 用的工作台地址（容器之间访问，Worker 注册 Webhook 时使用）；留空则不自动注册，只靠每 5 分钟的对账 | `http://caddy:8080` |

数据库密码请只用字母和数字（`make env` 生成的就是），否则需要在连接串中做 URL 编码。

- 修改了 `HTTP_PORT` 或改用域名访问时，要同时修改 `PUBLIC_BASE_URL`（或加入 `CORS_ALLOWED_ORIGINS`），
  否则登录会被拒绝（`403 CSRF_FAILED`）。改完后重新 `make up`。
- 升级代码后再运行一次 `make env`：它只会把 `.env.example` 里新增的变量补进 `.env`（新密钥随机生成），不改已有的值。
- 登录限流等参数也可以在 `.env` 中调整，变量见 `docs/decisions/2026-10-30-sprint-1-auth-contract.md` 第 9 节；
  加进 `.env` 后还要在 `compose.yaml` 的 api `environment` 中传入。
- 访问第三方服务的超时与重试也可以调整（同样要在 api `environment` 中传入）：`CONNECTOR_CONNECT_TIMEOUT_SECONDS`（默认 3）、
  `CONNECTOR_READ_TIMEOUT_SECONDS`（默认 10）、`CONNECTOR_READ_RETRIES`（只对读请求，默认 2；写请求从不自动重试）、
  `SYNC_STALE_AFTER_SECONDS`（最近一次成功同步超过多久显示为“数据可能过期”，默认 900）；
  Worker 的 `WEBHOOK_MAX_ATTEMPTS`（Webhook 事件最多尝试几次后进死信，默认 8）、
  `FULL_SYNC_INTERVAL_SECONDS`（多久做一次全量对账，默认 3600）——这两个要在 worker 的 `environment` 中传入；
  `RECONCILE_INTERVAL_MINUTES`（增量对账的间隔，1–30 分钟，默认 5）已在 compose.yaml 中传入。

## 4. 检查与排错

| 检查 | 预期 |
|---|---|
| `make ps` | 6 个服务都是 `Up`，其中 5 个显示 `(healthy)`（caddy 没有单独的健康检查） |
| `curl -s 127.0.0.1:8080/healthz` | `{"status":"ok"}` |
| `curl -s 127.0.0.1:8080/readyz` | `{"status":"ok","checks":{"database":"ok","redis":"ok"}}`；依赖不可用时返回 503 |
| `docker compose exec worker arq app.jobs.worker.WorkerSettings --check` | 退出码 0 |

常见问题：

- **`make up` 卡在等待健康检查**：`make logs` 查看是哪个服务没起来；`docker compose ps -a` 看退出码。
- **`/readyz` 返回 503**：响应里 `checks` 指出是数据库还是 Redis；对应服务的日志里有原因（API 日志只记录异常类型）。
- **端口 8080 被占用**：修改 `.env` 里的 `HTTP_PORT` 后重新 `make up`。
- **登录 / 安装返回 `403 CSRF_FAILED`**：浏览器地址与 `PUBLIC_BASE_URL`、`CORS_ALLOWED_ORIGINS` 都不一致，见第 3 节。
- **登录返回 `429 RATE_LIMITED`**：15 分钟内同一账户失败 5 次或同一 IP 失败 10 次，等 `Retry-After` 秒后自动解除。
  需要立即解除时清除计数（不会影响其他数据）：
  `docker compose exec redis sh -c "redis-cli --scan --pattern 'ratelimit:*' | xargs -r redis-cli del"`。
- **改了数据库密码后连不上**：PostgreSQL 只在第一次初始化数据卷时读取密码；已有数据卷时需要在数据库里修改密码，或者确认不需要数据后手动删除卷（会丢数据，不要作为常规操作）。

日志：所有服务输出到 stdout，由 Docker 的 json-file 驱动按 10 MB × 5 个文件轮转。API 与 Worker 的日志是 JSON，每条带 `request_id`；健康检查成功的请求只在 `LOG_LEVEL=DEBUG` 时记录。

## 5. 升级与停机

1. `git pull`
2. `make up`（重新构建镜像并滚动重建容器）
3. `make migrate`

停机用 `make down`，**不要**使用 `docker compose down -v`：`-v` 会删除数据卷。

## 6. 生产环境注意事项

- **HTTPS**：在 `infra/caddy/Caddyfile` 中把 `:8080` 换成域名、去掉 `auto_https off`，Caddy 会自动申请证书；
  同时在 `header` 块加入 `Strict-Transport-Security "max-age=31536000; includeSubDomains"`，并把 `HTTP_BIND` 设为 `0.0.0.0`、端口改为 80/443。
- **密钥**：生产环境用 Secret 管理工具注入，不要把真实值写进仓库或 Compose 文件。
- **`APP_ENV=production`**：关闭 `/api/v1/docs` 与 OpenAPI；Cookie 加 `Secure`（`PUBLIC_BASE_URL` 是 https 时也会加）；
  没有设置足够长的 `SESSION_SECRET` 时 API 拒绝启动。
- **客户端 IP**：Compose 中 API 只能经 Caddy 访问，因此设置了 `TRUST_FORWARDED_FOR=true`（登录限流按 `X-Forwarded-For`
  最右侧的地址计数）。如果绕过 Caddy 直接暴露 API，必须去掉这个设置，否则客户端可以伪造 IP 绕过限流。
- 备份恢复尚未实现（S6-04）；在此之前请自行用 `pg_dump` 做数据库备份。

## 7. 本机直接运行（开发）

不使用 Docker 时，需要本机的 Python 3.12 + uv、Node.js 22、PostgreSQL 16、Redis 7：

```bash
make setup                              # 生成 .env 并安装前后端依赖
export DATABASE_URL=postgresql+asyncpg://workspace:<密码>@localhost:5432/workspace
export REDIS_URL=redis://localhost:6379/0
export PUBLIC_BASE_URL=http://localhost:5173    # 浏览器访问的是 Vite 开发服务器
export SETUP_TOKEN=$(make -s setup-token)       # 首次安装需要
make migrate-local                      # 迁移本机数据库
make dev                                # API :8000 + 前端 :5173（/api 等路径代理到 API）
```

本机直接运行时 API 只读环境变量，不读取 `.env`，所以要像上面这样 `export` 需要的变量；
没有设置 `SESSION_SECRET` 时使用内置的开发密钥（仅限非生产环境）。

本机数据库只需要数据库 owner 权限，迁移不需要超级用户：

```sql
CREATE ROLE workspace LOGIN PASSWORD '<本地密码>';
CREATE DATABASE workspace OWNER workspace;
```

## 8. Vikunja（任务主源，独立部署）

Vikunja 用单独的 Compose 文件 `infra/vikunja/compose.yaml` 运行（规范第 11 节），版本固定为 **2.7.1**（镜像带 digest）。
版本核验、权限与安全取舍见 `docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`。

```bash
make up              # 先启动 core：它会创建 integrations 网络
make vikunja-up      # 启动 Vikunja；原生界面 http://localhost:3456
make vikunja-user    # 创建用户（自助注册已关闭）
make vikunja-down    # 停止，保留数据卷
```

- 配置在根目录 `.env`：`VIKUNJA_SERVICE_SECRET`（`make env` 随机生成；丢失后所有 Vikunja 登录与 Token 失效）、
  `VIKUNJA_PUBLIC_URL`、`VIKUNJA_BIND`、`VIKUNJA_PORT`。core 的 Compose 项目名不是 `my-workspace` 时，
  make 会按 `COMPOSE_PROJECT_NAME` 自动找到对应的 integrations 网络。
- 工作台访问 Vikunja 用 `http://vikunja:3456`（integrations 网络内）；Vikunja 把 Webhook 发到
  `http://caddy:8080/api/v1/webhooks/vikunja/<集成 ID>`，不经过对外端口。
- 为了让 Webhook 能发到私有地址，Compose 里打开了 `VIKUNJA_OUTGOINGREQUESTS_ALLOWNONROUTABLEIPS`；
  因此要保持自助注册关闭，只给可信的人创建 Vikunja 用户。多人使用时改用出站代理（见核验 ADR 第 6 节）。
- 数据在卷 `vikunja-db`（SQLite）与 `vikunja-files`；Vikunja 自带 `vikunja dump` / `restore` 备份命令，纳入 S6-04 的备份方案。
- 工作台连接 Vikunja 用的 API Token 在 Vikunja 原生界面的设置里创建，需要的最小权限见核验 ADR 第 3 节。
- 在工作台里添加集成（workspace admin）：服务地址填 `http://vikunja:3456`（容器内地址，已在 `INTEGRATION_ALLOWED_HOSTS` 里），
  浏览器地址填 `VIKUNJA_PUBLIC_URL`，再粘贴 Token；保存后点“测试连接”，通过后会立即同步全部项目与任务。
  接口见 `docs/api-contract.md` 的 Sprint 3 部分；Token 加密保存，之后不再显示。
- Vikunja 只按秒保存时间：同一秒内两个人修改同一个任务时，后写的一方会覆盖前者而不报冲突（核验 ADR 第 5.5 节）。
- **Vikunja 里的改动怎么回到工作台**（契约第 9、10.1 节）：
  - Token 带 `projects_webhooks` 权限时，测试连接通过后 Worker 自动为每个未归档项目注册 Webhook，
    Vikunja 经 `WEBHOOK_BASE_URL` 投递，改动通常一两秒内出现；之后新建的项目在每小时的全量对账时补注册。
  - Worker 每 5 分钟做一次增量对账、每小时做一次全量对账（发现删除），Webhook 丢失或没有 Webhook 权限时也能最终一致；
    集成页的“立即同步”触发一次全量同步。
  - 处理失败的事件会重试，多次失败后进死信（不会丢，也不会无限重试）。查看：
    `docker compose exec -T postgres psql -U workspace -d workspace -c "SELECT event_type, attempts, error_text, received_at FROM workspace.webhook_inbox WHERE status = 'dead_letter' ORDER BY received_at DESC LIMIT 20"`。
    `token_rejected` 表示 Token 失效或被收回权限：在 Vikunja 里重新创建 Token，粘贴到集成设置并重新测试连接。


## 9. AFFiNE（文档主源，V1 只做深链接）

工作台**不连接 AFFiNE**：V1 只保存你收藏的 AFFiNE 文档链接，以及你手工填写的标题和备注。
它不读取文档内容，也不调用 AFFiNE 的接口（规范 7.2）。核验结论见 `docs/decisions/2026-10-30-github-rest-and-affine-verification.md` 第 7 ~ 9 节。

- 本仓库不提供 AFFiNE 的 Compose。需要自己部署时，以官方自托管文档为准，版本锁定为 **0.27.4**：
  `ghcr.io/toeverything/affine:0.27.4@sha256:b649f5ce2384ffdf13c23bccf81d759e15973d59d0b0058af65d895a84373099`。
- 官方 Compose 有几处默认配置不安全，部署时要改：
  - PostgreSQL 用的是 `POSTGRES_HOST_AUTH_METHOD: trust`（不要密码）：改成设置密码，`DATABASE_URL` 带上密码；
  - `redis`、`pgvector/pgvector:pg16` 没有锁定版本：都锁定到具体版本；
  - 不要把 AFFiNE 加入工作台的 `backend` / `integrations` 网络，用单独的 Compose 项目。
- 0.27 起服务端配置以 `config.json` 为主。用 `server.externalUrl` 设置浏览器访问 AFFiNE 的地址；部署在子路径下时还要设置 `server.path`。
- 在工作台里填写的 **AFFiNE 外部地址**要和 `server.externalUrl` 完全一致，包括子路径。
  由 workspace admin 配置（接口 `POST /api/v1/knowledge/sources`，设置页入口由前端任务 T-0030 提供）；
  每个工作空间一个，以后改地址时，已有收藏的链接会自动改为新地址。
  收藏只接受这个地址下 `/workspace/<工作空间 ID>/<文档 ID>` 或 `/share/<工作空间 ID>/<文档 ID>` 形式的链接。
  在 AFFiNE 里打开文档后，复制浏览器地址栏里的地址即可。
- 外部地址必须是 `https://`；内网开发时可以用 `http://`，但必须显式配置。
- 备份：AFFiNE 的 PostgreSQL、`storage` 和 `config` 三者要一起备份（规范 11.4）。工作台的备份只包含收藏链接。
- 许可证：AFFiNE 服务端适用 Enterprise Edition 许可，其中不属于 Community Edition 的部分在生产环境使用需要订阅。部署前请自行确认（核验 ADR 第 7 节）。

## 10. GitHub（只读）

工作台用 GitHub REST API（版本 `2026-03-10`）**只读**同步你指定仓库的 Issue、PR 和 Actions 最近运行。
V1 只支持 github.com，不注册 GitHub Webhook。核验结论见上面同一个 ADR 的第 2 ~ 6 节。

- 在 GitHub 的 Settings → Developer settings → Personal access tokens → **Fine-grained tokens** 里创建 Token：
  - Resource owner：仓库所属的用户或组织（组织可能要求审批，审批前只能读公开仓库）；
  - Repository access：**Only select repositories**，只选要在工作台里看的仓库；
  - Repository permissions 全部设为 **Read-only**：Metadata（自动带上）、Issues、Pull requests、Actions；其他权限都不要给；
  - 建议设置过期时间，并在集成设置里填上同样的过期时间。
- 不要用经典 Token（`ghp_` 开头）：它能访问你的所有仓库。测试连接时如果识别出经典 Token，会提示改用细粒度 Token。
- `api.github.com` 是公网地址，不需要加进 `INTEGRATION_ALLOWED_HOSTS`。服务器要能访问 `https://api.github.com`。
- 限流：每个 Token 每小时 5,000 次，同一用户的其他调用方式共用这个额度。
  - 工作台串行请求，并用条件请求（数据没变时不消耗额度）；
  - 被限流时页面显示“GitHub 限流中”和预计恢复时间，不会显示成空列表；
  - Token 失效时显示“需要重新连接”，不会反复重试：GitHub 会因为反复使用无效凭证暂时拒绝该用户的所有请求。
- 连接（workspace admin）：在集成设置里新建 GitHub 集成，粘贴 Token，列出要同步的仓库（`owner/repo`，最多 20 个），
  保存后点“测试连接”。测试会逐个仓库检查四项读权限，缺哪一项会单独标出；通过后立即同步一次。
  接口见 `docs/api-contract.md` 的 Sprint 4 部分（`POST /api/v1/integrations`，`provider: github`）。
- 同步：Worker 随周期对账同步（`RECONCILE_INTERVAL_MINUTES`，默认 5 分钟），每小时做一次全量。
  - 每个仓库同步 Issue、PR 和最近 30 次 Actions 运行。失败的运行会读一次 jobs，记下失败的 job 和 step 名称，不读日志。
  - 增量同步时，三个列表的第一页都带 `ETag`，没有变化时返回 304，不消耗额度。
  - 从集成里移除的仓库，以及 GitHub 上删除的 Issue / PR，会在全量同步后从工作台消失。
- 被限流时记下恢复时间，期间不再请求 GitHub；Token 失效后集成变为 `unauthorized`，不再自动同步，更换 Token 并重新测试后恢复。
- 测试与 CI 验收可以用 `GITHUB_API_URL` 把地址改成模拟服务（同时把它的主机名加进 `INTEGRATION_ALLOWED_HOSTS`）；生产环境不要设置。
