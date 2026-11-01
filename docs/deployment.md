# 部署与运行手册

> 状态：Sprint 6（2026-10-31）。Sprint 0 ~ 5 的功能已验收（`docs/acceptance/`）。
> - 升级与回滚：第 5 节；
> - 备份与隔离恢复：第 14 节；
> - 安全扫描、完整冒烟与性能基准：第 15 节；
> - V2：OpenClaw 接入入口第 16 节，OpenIM 第 17 节；
> - 所有组件的锁定版本与许可证：`docs/versions.md`；
> - 发布前逐项检查：`docs/release-checklist.md`。
>
> AI 助手（S6-01 ~ S6-03）等人类选定 AI 服务后再做，目前页面上只有占位说明。

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

第一次打开首页会自动进入安装页，填写邮箱、显示名、密码（12–128 个字符）、工作空间名称和安装凭据即可。
也可以直接调用接口：

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
| `make smoke-full` | 完整端到端冒烟：全部读接口与页面、核心服务、SSE、写入往返、登出（第 15.2 节） |
| `make backup` / `make restore-verify` | 加密备份 / 在隔离环境恢复演练（第 14 节） |
| `make scan` / `make perf` | 安全扫描 / 性能基准（第 15 节） |
| `make ps` / `make logs` | 查看服务状态 / 跟踪日志 |
| `make down` | 停止并删除容器，**保留数据卷**（数据不会丢） |

## 2. 服务与网络

| 服务 | 镜像 | 作用 | 网络 | 对外端口 |
|---|---|---|---|---|
| caddy | `caddy:2.11.7-alpine` | 统一入口：路由、安全响应头、请求体限制 | frontend + integrations | `127.0.0.1:8080`（可配置） |
| web | `my-workspace-web:local`（`apps/web/Dockerfile`） | 前端静态文件（nginx，非 root） | frontend | 无 |
| api | `my-workspace-api:local`（`services/api/Dockerfile`） | FastAPI BFF（非 root） | backend + frontend + integrations + monitoring | 无 |
| worker | 同 api 镜像 | ARQ 后台作业 | backend + integrations + egress | 无 |
| postgres | `postgres:16.15-alpine3.24` | 数据库 | backend | 无 |
| redis | `redis:7.4.12-alpine3.21` | 缓存 / 作业队列 | backend | 无 |
| prometheus（可选） | `prom/prometheus:v3.15.1` | 指标（`--profile monitoring`，第 12 节） | monitoring | 无 |
| grafana（可选） | `grafana/grafana:13.2.5` | 仪表盘与告警（外链） | monitoring + monitoring-public | `127.0.0.1:3000`（可配置） |

- `backend` 是内部网络（不通外网）；数据库和 Redis 只在这个网络里，宿主机和外网都连不到。
- `integrations` 也是内部网络，只有 caddy、api、worker 加入，供独立部署的第三方服务（Vikunja 等，见第 8 节）与工作台互通；
  第三方服务碰不到 backend 里的数据库和 Redis。
- `egress` 只有 worker 加入，用来访问外部托管的服务（GitHub 的周期同步要访问 `https://api.github.com`）；
  api 经 `frontend` 出网。出站地址仍由 Connector 的 SSRF 检查限制（私有地址只允许 `INTEGRATION_ALLOWED_HOSTS` 里的主机）。
- `monitoring` 是内部网络，只有 api、prometheus、grafana 加入；`monitoring-public` 只用来发布 Grafana 的端口。
- api、worker、web、prometheus、grafana 以只读文件系统运行，去掉全部 Linux capabilities，禁止提权。
- Caddy 路由：`/api/*`、`/healthz` → api；`/readyz` 只接受内网来源（公网返回 404）；其余 → web。

数据卷：`postgres-data`、`redis-data`、`caddy-data`、`caddy-config`，启用监控后还有 `prometheus-data`、`grafana-data`
（Compose 项目名 `my-workspace`，卷名带此前缀）。

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
| `CREDENTIAL_ENCRYPTION_KEY` | 第三方连接凭证与密钥库的加密密钥（64 位十六进制）；格式错误时 API 拒绝启动。**丢失后已保存的凭证无法解密**，请与数据库备份分开保存；更换按第 6.1 节轮换 | 随机 |
| `CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS` | 轮换期间保留的旧密钥（逗号分隔，只用于解密）；平时为空 | 空 |
| `INTEGRATION_ALLOWED_HOSTS` | 允许工作台访问的内网主机名，逗号分隔。第三方服务的地址解析到私有 / 内网 / 链路本地地址时一律拒绝（SSRF 防护），除非主机名在这里 | `vikunja` |
| `WEBHOOK_BASE_URL` | Vikunja 投递 Webhook 用的工作台地址（容器之间访问，Worker 注册 Webhook 时使用）；留空则不自动注册，只靠每 5 分钟的对账 | `http://caddy:8080` |
| `PROMETHEUS_URL` / `GRAFANA_URL` | 可选监控的内部地址（第 12 节）；留空表示没有部署，运维页显示“未部署” | 空 |
| `GRAFANA_DASHBOARD_URL` | 运维页 Grafana 外链（浏览器访问的地址） | 空 |
| `GRAFANA_ADMIN_PASSWORD` | Grafana 管理员密码；为空时 Grafana 拒绝启动 | 随机 |
| `LOG_REQUEST_PATH` | 访问日志的路径：`path` 为实际路径（不含查询串），`route` 只记录路由模板 | `path` |

数据库密码请只用字母和数字（`make env` 生成的就是），否则需要在连接串中做 URL 编码。

- 修改了 `HTTP_PORT` 或改用域名访问时，要同时修改 `PUBLIC_BASE_URL`（或加入 `CORS_ALLOWED_ORIGINS`），
  否则登录会被拒绝（`403 CSRF_FAILED`）。改完后重新 `make up`。
- 升级代码后再运行一次 `make env`：它只会把 `.env.example` 里新增的变量补进 `.env`（新密钥随机生成），不改已有的值。
- 登录限流等参数也可以在 `.env` 中调整，变量见 `docs/decisions/2026-10-30-sprint-1-auth-contract.md` 第 9 节；
  加进 `.env` 后还要在 `compose.yaml` 的 api `environment` 中传入。
- 访问第三方服务的超时与重试也可以调整（同样要在 api `environment` 中传入）：`CONNECTOR_CONNECT_TIMEOUT_SECONDS`（默认 3）、
  `CONNECTOR_READ_TIMEOUT_SECONDS`（默认 10）、`CONNECTOR_READ_RETRIES`（只对读请求，默认 2；写请求从不自动重试）、
  `SYNC_STALE_AFTER_SECONDS`（最近一次成功同步超过多久显示为“数据可能过期”，默认 900）；
- 数据库连接池与首页聚合的并发（同样要在 api `environment` 中传入）：`DATABASE_POOL_SIZE`（每个进程的常驻连接，默认 10）、
  `DATABASE_MAX_OVERFLOW`（高峰时额外允许的连接，默认 10）、`DASHBOARD_OVERVIEW_CONCURRENCY`（同时进行的首页聚合数，默认 3；
  每次聚合约占 5 个连接，超出的请求排队，不会让模块超时）。api 与 worker 各有一个连接池，两者之和要小于 PostgreSQL 的
  `max_connections`（默认 100）。
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

## 5. 升级、回滚与停机

发布前的逐项检查见 `docs/release-checklist.md`；所有组件的锁定版本见 `docs/versions.md`。

### 5.1 升级

1. 读新版本的变更：`docs/versions.md` 与 `services/api/migrations/versions/` 有没有变化，需不需要新的 `.env` 变量。
2. 记下当前版本，回滚时要用：
   - `git rev-parse HEAD`；
   - `docker compose exec -T api alembic current`，例如 `0010 (head)`。
3. `make backup`，记下输出里的快照 ID（`备份完成：<快照 ID> <时间>`）；把当前的 `.env` 另存一份。
4. `git fetch && git checkout <新版本的 tag 或提交>`，然后 `make env`：只补上新增的变量，不改已有的值。
5. `docker compose build`：先构建新镜像，旧容器照常服务。
6. `docker compose run --rm --no-deps -T api alembic upgrade head`：用新镜像执行迁移，此时旧版本仍在运行。
   - 迁移只新增对象（第 5.2 节），旧代码不受影响。
7. `make up`：用新镜像重建容器，等待全部健康检查通过。
8. `make smoke`。装了 `tests/e2e` 的依赖时再跑 `make smoke-full`。
9. 登录核对首页、任务、日历等常用页面，查看运维页的服务状态。

Vikunja、n8n 等独立部署的组件分开升级：
- 先各自备份（Vikunja 用 `vikunja dump`；n8n 备份数据卷与 `N8N_ENCRYPTION_KEY`）；
- 再改 `infra/*/compose.yaml` 里的 tag + digest，按对应的核验 ADR 重新验证。

### 5.2 数据库迁移的约定

- 迁移 `0001` ~ `0010` 都**只新增**表、索引、触发器，每个都有 downgrade。
  CI 每次都跑 `upgrade head → downgrade base → upgrade head`（`ci-api.yml`），恢复演练也会在恢复出来的数据上执行 `upgrade head`。
- 以后的迁移继续遵守“先扩展、后收缩”：
  - 新版本只加表、加列（可为空或有默认值），旧代码照常运行；
  - 删列、改类型、改名分两次发布：先让代码不再使用，下一次发布再删除。
- **会改写或删除已有数据的迁移不可逆**：downgrade 无法找回数据。这类迁移要写明，并由人类决定后再合并（D-0004 第 3 节）；回滚只能从备份恢复。
- downgrade 会删除这次迁移新增的表，**连同其中的数据**。例如退回 `0009` 会删除自动化的连接、白名单与执行记录。

### 5.3 回滚

按升级时走到了哪一步，选择对应的做法：

| 情况 | 做法 |
|---|---|
| 迁移前就失败了（构建失败、`make env` 后发现问题） | `git checkout <旧版本>`，旧容器一直在运行，不用做别的 |
| 已经迁移，但这次没有新的迁移 | `git checkout <旧版本>` → `make up` → `make smoke` |
| 已经迁移，有新的迁移，而且只新增对象 | **先用新版本的代码**执行 `docker compose exec -T api alembic downgrade <升级前的版本号>`（旧代码里没有新迁移的 downgrade 脚本），再 `git checkout <旧版本>` → `make up` → `make smoke`。新表里在这期间写入的数据会丢失 |
| downgrade 失败、迁移不可逆，或数据已经出问题 | 从升级前的快照恢复：<br>1. `scripts/restore-verify.sh <快照 ID>` 先在隔离环境验证这个快照；<br>2. `git checkout <旧版本>`，放回升级前的 `.env`；<br>3. `make down`，再按第 14.5 节恢复（把 `latest` 换成这个快照 ID）；<br>4. `make up && make smoke`。<br>升级之后写入的数据会丢失 |
| 第三方镜像升级后有问题 | 改回旧的 tag + digest（`git revert` 那次提交）→ `make up`。**PostgreSQL 换了大版本时不能这样回滚**：数据目录不兼容，只能从备份恢复 |

- 回滚后，在任务日志或 issue 里记下原因、做法与丢失的数据范围。
- 修复后按升级步骤重新发布。

### 5.4 停机

停机用 `make down`。**不要**使用 `docker compose down -v`：`-v` 会删除数据卷。

## 6. 生产环境注意事项

- **HTTPS**：在 `infra/caddy/Caddyfile` 中把 `:8080` 换成域名、去掉 `auto_https off`，Caddy 会自动申请证书；
  同时在 `header` 块加入 `Strict-Transport-Security "max-age=31536000; includeSubDomains"`，并把 `HTTP_BIND` 设为 `0.0.0.0`、端口改为 80/443。
- **密钥**：生产环境用 Secret 管理工具注入，不要把真实值写进仓库或 Compose 文件。
- **`APP_ENV=production`**：关闭 `/api/v1/docs` 与 OpenAPI；Cookie 加 `Secure`（`PUBLIC_BASE_URL` 是 https 时也会加）；
  没有设置足够长的 `SESSION_SECRET` 时 API 拒绝启动。
- **客户端 IP**：Compose 中 API 只能经 Caddy 访问，因此设置了 `TRUST_FORWARDED_FOR=true`（登录限流按 `X-Forwarded-For`
  最右侧的地址计数）。如果绕过 Caddy 直接暴露 API，必须去掉这个设置，否则客户端可以伪造 IP 绕过限流。
- **备份**：把 `BACKUP_REPOSITORY` 配成异地仓库，定时运行 `make backup`，每月运行一次 `make restore-verify` 演练（第 14 节）。
- **版本锁定**：所有镜像都是 tag + digest（`docs/versions.md`）。不要改成 `latest`；升级按第 5 节和 `docs/release-checklist.md` 进行。
- **许可证**：Grafana、Vikunja 是 AGPL-3.0，n8n 是 Sustainable Use License，Redis 7.4 是 RSALv2 / SSPLv1。
  自托管自用没有问题；要对外提供服务或分发之前，先看 `docs/versions.md` 第 5 节。
- **发布前**：按 `docs/release-checklist.md` 逐项检查（CI、扫描、版本锁定、备份演练、`.env` 与密钥轮换、性能、回滚方案）。

### 6.1 加密密钥轮换

凭证密文的引用里带着加密它的密钥 ID（`enc:v1:<key_id>:…`），所以新旧密钥可以并存，按记录逐条换成新密钥：

1. 生成新密钥：`python3 -c "import secrets; print(secrets.token_hex(32))"`。
2. 改 `.env`：把原来的 `CREDENTIAL_ENCRYPTION_KEY` 移到 `CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS`，新密钥填进 `CREDENTIAL_ENCRYPTION_KEY`；
   然后 `docker compose up -d api worker`。这时新旧密文都能解密，新写入的都用新密钥。
3. 先预演，再重新加密：
   ```
   docker compose exec -T api python -m app.commands.rotate_credentials --dry-run
   docker compose exec -T api python -m app.commands.rotate_credentials
   ```
   覆盖集成凭证、集成的 Webhook 密钥、n8n 的 API Key 与共享密钥、OpenClaw 密钥库、内部 CA 私钥。
   可以重复运行；只改密文，不改记录的 `updated_at` 和版本号。
4. 输出“仍是旧密钥：0；失败：0”（退出码 0）之后，清空 `CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS`，再 `docker compose up -d api worker`。
   失败的记录原样保留：通常是缺了更早的某把旧密钥，把它也加进 `CREDENTIAL_ENCRYPTION_KEYS_PREVIOUS` 再运行一次。
5. 轮换前后各做一次 `make backup`；旧的备份要用旧密钥才能解密，旧密钥按备份的保留期一起保管。

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
- 数据在卷 `vikunja-db`（SQLite）与 `vikunja-files`。Vikunja 独立部署、各自备份：用它自带的 `vikunja dump` / `restore`（第 14.1 节）；`make backup` 只备份工作台的数据库。
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
- 测试与 CI 验收可以在 `.env` 里用 `WORKSPACE_GITHUB_API_URL` 把地址改成模拟服务（Compose 把它作为
  `GITHUB_API_URL` 传给 api 与 worker；同时把模拟服务的主机名加进 `INTEGRATION_ALLOWED_HOSTS`）；生产环境不要设置。
  `.env` 里不用 `GITHUB_API_URL` 这个名字：GitHub Actions 等环境会在 shell 里设置它，而 Compose 插值时 shell 变量优先于 `.env`。

## 11. 跨资源搜索

- 顶栏搜索查的是工作台自己的索引：Vikunja 的任务与项目、GitHub 的仓库 / Issue / PR、AFFiNE 收藏（标题与备注）、本地日程。
  - 不搜 AFFiNE 文档正文，也不搜 Actions 运行。
  - 只返回你能看到的内容：别人的私有收藏、其他工作空间的数据都搜不到。
- 索引由数据库触发器随写入更新，删除或同步掉的内容会立刻搜不到，平时不需要维护。
  怀疑索引与数据不一致时可以重建：`docker compose exec -T api python -m app.commands.reindex_search`。
- 中文按字面包含匹配，不做分词，所以相关性有限：能找到包含查询文字的结果，排序只区分“标题完全一致 / 标题开头 / 标题包含 / 只在正文里”。
  英文支持词干，例如 deploy 能找到 deploying。


## 12. 监控与自动化（Sprint 5，可选组件）

版本与核验结果见 `docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`，
接口约定见 `docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md` 第 3 节。

### 12.1 启用监控（Prometheus + Grafana）

监控是 `compose.yaml` 里的可选 profile（规范第 11.1 节），配置文件在 `infra/monitoring/`：

1. `make env`：会补上新变量，并随机生成 `GRAFANA_ADMIN_PASSWORD`（没有它 Grafana 拒绝启动，避免默认密码 `admin`）。
2. 在 `.env` 里填上三个地址，让工作台知道监控已部署：

   ```dotenv
   PROMETHEUS_URL=http://prometheus:9090
   GRAFANA_URL=http://grafana:3000
   GRAFANA_DASHBOARD_URL=http://localhost:3000/d/workspace-overview/my-workspace
   ```

   - 前两个是容器之间的内部地址，只给 api 用，不会下发到浏览器。
   - 第三个是浏览器打开的外链；换了域名或端口时同时改 `GRAFANA_PUBLIC_URL`。
3. 启动：

   ```bash
   docker compose --profile monitoring up -d --wait
   ```

4. 检查：运维页（`/operations`）里 Prometheus、Grafana 显示为正常；Grafana 用 `admin` 和 `.env` 里的密码登录，
   “My Workspace” 文件夹里有预置的仪表盘。
   - 不填这三个地址时，运维页显示“未部署”，工作台照常使用。
   - 停用：从 `.env` 删掉三个地址，然后执行：

     ```bash
     docker compose --profile monitoring stop prometheus grafana
     ```

     数据卷 `prometheus-data`、`grafana-data` 保留。

### 12.2 安全边界

- **Prometheus**（`prom/prometheus:v3.15.1`，按 digest 锁定）
  - 只在内部网络 `monitoring` 里，不发布端口，不经 Caddy 转发：它的配置接口在默认情况下可以直接读到。
  - 不加 `--web.enable-admin-api`、`--web.enable-lifecycle`。
  - 保留 7 天：`--storage.tsdb.retention.time=7d`（S5-05）。
  - 抓取 api 的 `/metrics`（`infra/monitoring/prometheus.yml`）。job 名必须以 `workspace-` 开头，运维页的“服务在线”按它查询。
- **api 的 `/metrics`**
  - Caddy 只转发 `/api/*`、`/healthz`、`/readyz`，所以从外部访问 `/metrics` 只会得到前端页面。
  - 指标标签只有方法、路由模板和状态码类别，不含原始路径、查询串、Token。
  - 容器之间可以访问 `api:8000/metrics`，内容是请求计数与耗时，以及下面的 V2 指标（全部工作空间合计）。
- **V2 指标**（方案第 6.1 节，T-0062；`app/domain/ops_metrics.py`）：

  | 指标 | 类型 | 含义 |
  |---|---|---|
  | `workspace_agent_instances{status}` | gauge | 登记的 OpenClaw 实例数：`pending` / `online` / `offline` / `disabled` |
  | `workspace_agent_secret_resolves_total{result}` | counter | 实例取密钥（每个密钥 ID 一次）：`ok` / `not_found` / `rate_limited` |
  | `workspace_agent_auth_failures_total{ingress}` | counter | 实例认证失败（凭证无效、吊销、停用、入口不符、缺客户端证书），按 `intranet` / `public` / `none` |
  | `workspace_mcp_tool_calls_total{tool,outcome}` | counter | MCP 工具调用；`outcome` 为 `ok` 或工具错误码（小写），不认识的工具名记为 `unknown` |
  | `workspace_ai_pending_approvals` | gauge | 等待人工审批、还没过期的写操作 |
  | `workspace_im_inbox_events{status}` | gauge | OpenIM 回调收件箱里没处理完的事件：`pending` / `processing` / `retry` / `dead_letter` |
  | `workspace_im_reconcile_filled_messages_total` | counter | 周期对账补进投影的消息（回调丢失或延迟时才会增加） |
  | `workspace_im_relay_pauses_total` | counter | 群聊因实例连续发言达到上限而暂停自动接力的次数 |

  - 计数类记在 Redis（api 的多个进程和 Worker 都会写），Redis 清空后从 0 重新计数；状态类在抓取时查 PostgreSQL。
    数据库或 Redis 不可用时 `/metrics` 只返回请求指标。
  - 标签里没有实例 ID、工作空间 ID、Token 或用户输入；单个实例的状态看工作台的运维页或实例页。
  - Grafana 的预置仪表盘有对应的四个面板；运维页的服务列表里，OpenIM 与本工作空间的实例单独成组（实例只对 owner / admin 显示）。
- **Grafana**（`grafana/grafana:13.2.5`）
  - 匿名访问、注册、嵌入（`allow_embedding`）都关闭，不向外报告统计；数据源与仪表盘是只读的预置文件。
  - 端口默认只绑定本机（`GRAFANA_BIND=127.0.0.1`）。对外开放时放在 HTTPS 反向代理后面，并设置 `GRAFANA_PUBLIC_URL`。
  - 工作台只生成外链，不调用 Grafana API，不保存 Grafana 的账号或 Token。
- **浏览器**只拿到 BFF 聚合好的预设视图，不能提交 PromQL。
  - Prometheus 不可达时，运维页显示最近一次的结果并标为“可能过期”；没有结果时显示不可用，不显示 0。

### 12.3 日志与保留（S5-05）

- 访问日志不记录查询串。设置 `LOG_REQUEST_PATH=route` 后，连路径里的 ID 也不记录，只记录路由模板，
  例如 `/api/v1/tasks/{task_id}`。
- 容器日志由 Docker 的 `json-file` 按大小轮转（每个服务 5 × 10 MB），不是按天保留。
  需要严格的“7 天”时，在宿主机的日志收集里按时间清理。
- 指标在 Prometheus 里保留 7 天。长期趋势请在 Grafana 里另行配置聚合或远端存储（不在 V1 范围）。

### 12.4 n8n（自动化，可选）

n8n 是独立的 Compose（`infra/n8n/compose.yaml`），与 Vikunja 一样加入 core 的 `integrations` 网络。
接口约定见 Sprint 5 契约第 6 节，触发与执行记录的实测见 `docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md`。

1. 启动：

   ```bash
   make up
   ```

   ```bash
   make n8n-up
   ```

   `make env` 会随机生成 `N8N_ENCRYPTION_KEY`，请与数据库备份分开保存。
2. 允许工作台访问 n8n：在 `.env` 的 `INTEGRATION_ALLOWED_HOSTS` 里加上 `n8n`（新生成的 `.env` 默认就是 `vikunja,n8n`），然后 `make up`。
3. 打开编辑器（默认 `http://localhost:5678/`），创建 owner 账号，然后：
   - **API Key**：Settings → n8n API → Create an API key，只勾选 `workflow:list`、`workflow:read`、`execution:list`、`execution:read`。
     工作台不会尝试写入，所以测试连接发现不了权限过大的 Key，请只勾选这四项。
   - **共享密钥**：新建 Header Auth 凭据，Name 填 `X-Workspace-Trigger`，Value 用一段随机字符串（至少 16 个字符）。
   - **工作流**：以 Webhook 节点开头：
     - HTTP Method 为 `POST`；
     - Path 是一个固定的名字（只用字母、数字、`-`、`_`）；
     - Authentication 选 Header Auth，并选上面的凭据；
     - Respond 选 Immediately。
     保存并激活。
4. 在工作台的自动化页（admin）：
   - 填写连接：`base_url` 为 `http://n8n:5678`，`public_url` 为浏览器打开编辑器的地址，再填 API Key 与共享密钥；
   - 测试连接；
   - 登记白名单：工作流 ID 在编辑器地址栏的 `/workflow/<ID>` 里，Webhook 路径就是上面的 Path，并声明参数。
5. 工作流收到的请求体是 `{"workspace_run_id": "<执行记录 ID>", "params": {…}}`。
   - 在 n8n 里用 `{{ $json.body.params.<参数名> }}` 读取参数；
   - 只有登记时声明过的参数才会发过来。

注意：

- **版本**：工作台读不到 n8n 的版本号（n8n 不提供）。测试连接时会给出警告，请确认部署的是核验过的 2.45.7，`infra/n8n/compose.yaml` 已按 digest 锁定。
- **执行记录**：Worker 每分钟对账一次。
  - 按触发请求的 `traceparent` 找到对应的 n8n 执行；
  - 失败时只读 n8n 遮蔽后的节点名与错误类型作为摘要，错误正文请到编辑器里看。
  - 触发后 15 分钟仍找不到对应执行的，标为“无法确认是否执行”，并通知触发者。
- **重复执行**：触发请求结果未知（超时、连接中断）时不会自动重发。同一个 Idempotency-Key 的重复请求只会返回原来的执行记录。
- **执行数据**：n8n 自己保存的执行数据由 n8n 管理，工作台不读取、不保存参数与输出。需要时在 n8n 里设置执行数据的保留期与遮蔽策略。

- **n8n**（`n8nio/n8n:2.45.7`）
  - 必须设 `N8N_LISTEN_ADDRESS=0.0.0.0`：没有 IPv6 的网络里，默认的 `::` 会让容器直接退出。
  - 健康检查用 `/healthz/readiness`：`/healthz` 在数据库迁移完成前就返回 ok。
  - 固定 `N8N_ENCRYPTION_KEY`，丢失后已存的凭据无法解密。
  - 给工作台用的 API Key 只勾选 `workflow:list`、`workflow:read`、`execution:list`、`execution:read`。
  - 白名单工作流用 Webhook 节点加 Header Auth 作为入口。
  - n8n 采用 Sustainable Use License：对外商业托管或让其他客户配置工作流之前，要重新核验许可证。

## 13. 通知与实时更新（Sprint 5）

约定见 `docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md` 第 4、5 节。

- **通知**存在 PostgreSQL 的 `notifications` 表（迁移 0009）。
  - 来源：
    - 集成授权失效、无法连接、恢复，以及 GitHub 限流，发给工作空间的 owner / admin；
    - 服务健康告警（见下）；
    - 自动化的执行结果，发给触发者（T-0037）。
  - 同一件事一小时内只通知一次。
  - 保留期：已读的保留 `NOTIFICATION_RETENTION_DAYS` 天（默认 90），未读的保留两倍时间。Worker 每小时第 17 分钟清理。
- **服务健康告警**（S5-02）：
  - Worker 每分钟检查 api（`API_INTERNAL_URL=http://api:8000` 的 `/readyz`）、Prometheus、Grafana；
  - 连续两次不可用时通知管理员，恢复时再通知一次；
  - 没有部署的组件不检查。
  - PostgreSQL 不可用时通知写不进去，Redis 不可用时 Worker 无法调度：这两项只在运维页显示。
- **SSE**（`/api/v1/events/stream`）：
  - 每个工作空间一条 Redis Stream（`events:ws:<ID>`），保留最近 500 条，24 小时没有新事件就过期。
  - 断线重连时按 `Last-Event-ID` 补发；超出保留范围时发 `reset`，页面会全部重新拉取。
  - Caddy 对这个路径不压缩、不缓冲（`infra/caddy/Caddyfile`，`flush_interval -1`）。
    如果前面还有别的反向代理（例如云负载均衡），也要关闭对 `text/event-stream` 的缓冲，
    并把空闲超时设为 30 秒以上（服务端每 15 秒发一次心跳）。
  - 连接上限（每个 api 进程，可在 api 的 `environment` 中调整）：
    - `SSE_MAX_CONNECTIONS`（默认 200）、`SSE_MAX_CONNECTIONS_PER_USER`（默认 5），超出时返回 `429`；
    - 单个连接最长 `SSE_MAX_SECONDS` 秒（默认 600），到期后浏览器自动重连；
    - 每 `SSE_RECHECK_SECONDS` 秒（默认 60）复核会话与成员身份，登出或被移出工作空间后停止推送。
  - SSE 不是可靠队列：页面重连、重新可见时都会重新拉取数据。

## 14. 备份与恢复（S6-04）

工具与格式的核验见 `docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md`。

### 14.1 备份什么

- **进备份**：工作台的 PostgreSQL 数据库（`pg_dump` custom 格式，备份时不用停服务），以及一份记录迁移版本与时间的 `backup-info.json`。
- **OpenIM 在运行时也进备份**（第 17.5 节）：MongoDB 的 `openim_v3` 库（`mongodump` 归档）与 Garage（元数据快照、节点身份与数据块）。
  OpenIM 的 Kafka、Redis、etcd 不备份：Kafka 只是中转，Redis 是缓存与在线令牌，etcd 只做服务发现。
- **不进备份**：
  - `.env`：里面有解密凭证的 `CREDENTIAL_ENCRYPTION_KEY`、会话密钥和备份仓库的密码。
    把它和备份分开保管（例如放进密码管理器），**不要只留在这台服务器上**。丢了它，备份里保存的第三方凭证就无法解密，备份本身也打不开。
  - Redis：只有缓存、任务队列、SSE 事件与限流计数，丢失后会自动重建；会话与幂等记录都在 PostgreSQL 里。
  - 独立部署的组件各自备份：Vikunja 用 `vikunja dump`（第 8 节）；n8n 备份它的数据卷与 `N8N_ENCRYPTION_KEY`；
    AFFiNE 的 PostgreSQL、storage、config 要一起备份（第 9 节）。

### 14.2 配置（`.env`）

| 变量 | 作用 | 默认 |
|---|---|---|
| `BACKUP_REPOSITORY` | restic 仓库。本地目录只适合测试，不算异地；生产用 `s3:https://…`、`sftp:…`、`rest:…` 等 | `./backups/restic` |
| `BACKUP_PASSWORD` | 仓库密码，`make env` 随机生成。**丢失就无法恢复** | 随机 |
| `BACKUP_KEEP_DAILY` / `BACKUP_KEEP_WEEKLY` / `BACKUP_KEEP_MONTHLY` | 保留时长：最近 7 天每天一份、30 天内每周一份、90 天内每月一份 | `7d` / `30d` / `90d` |
| `RESTORE_HTTP_PORT` | `make restore-verify` 临时环境的端口 | `18080` |

S3 仓库的凭据（`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_DEFAULT_REGION`）只从环境变量读取，不写进 `.env`。

### 14.3 命令

- `make backup`：
  - `pg_dump` 生成归档，用 `pg_restore --list` 确认完整；
  - 写进 restic 仓库（加密）；
  - 按保留策略清理旧快照（`forget --prune`），再 `check` 仓库。
  - 归档只在权限 700 的临时目录里短暂存在，结束后删除。
  - 第一次运行时自动初始化仓库。
- `make restore-verify`（恢复演练）：
  - 在**另一个 Compose 项目**（`<项目名>-restore-verify`，端口 `RESTORE_HTTP_PORT`，自己的网络与数据卷）里恢复最新快照；
  - 执行 `alembic upgrade head`，确认迁移能在恢复出来的数据上运行；
  - 冒烟检查（`scripts/smoke.sh`）与数据检查（用户、工作空间、仪表盘不为空）；
  - 快照里有 OpenIM 时，在不联网的临时容器里分别恢复 MongoDB 与 Garage，核对文档数与对象数与备份时一致；
  - 结束后删除这个临时项目。正在运行的栈不受影响。
  - 临时环境**不启动 Worker**，恢复出来的集成凭证不会去连接真实的第三方服务。
  - `scripts/restore-verify.sh --keep [快照 ID]` 会保留临时环境，便于登录核对；看完用 `scripts/restore-verify.sh --cleanup` 删除。
- CI 的 `ci-openim` 对 OpenIM 单独跑一遍“导出 + 隔离恢复检查”（`scripts/openim_backup.sh check`）。
- CI 的 A15 验收（`tests/e2e/backup_acceptance.py`）：
  - 写入数据后备份，再改动正在运行的栈；
  - 在临时环境里恢复后，用原来的账号登录，核对显示名、首页配置、日程与审计日志都是备份时的状态。

### 14.4 定时备份与每月演练

- **每天备份**：例如 crontab 加一行：`17 3 * * * cd /srv/my-workspace && make backup >> backups/backup.log 2>&1`。失败时 `make backup` 以非零状态退出，可以接到监控或邮件。
- **每月演练**：运行 `make restore-verify`，把结果（快照 ID、耗时、是否通过）记下来。本机实测：恢复约 50 个用户的数据库并通过检查，约 26 秒。

### 14.5 真实恢复（服务器损坏时）

1. 在新机器上 `git clone` 并检出与备份相同或更新的版本（`backup-info.json` 里有备份时的提交号与迁移版本）。
2. 放回单独保管的 `.env`（同一个 `CREDENTIAL_ENCRYPTION_KEY` 才能解密已保存的第三方凭证）。
3. `docker compose up -d --wait postgres redis`。
4. 取出快照：`docker run --rm -e RESTIC_REPOSITORY=… -e RESTIC_PASSWORD_FILE=… restic/restic:0.19.1@sha256:… restore latest --target <目录>`（写法同 `scripts/backup_common.sh`）。
5. `docker compose exec -T postgres pg_restore -U <POSTGRES_USER> -d <POSTGRES_DB> --exit-on-error < <目录>/backup/workspace.dump`。
6. `make up && make migrate && make smoke`，然后登录核对。
7. 部署了 OpenIM 的，按第 17.5 节恢复 OpenIM。

## 15. 发布前检查：安全扫描、完整冒烟与性能基准（S6-05）

工具、版本与判定规则的核验见 `docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md` 第 5、6、8 节。
三项都已纳入 CI：扫描在 `.github/workflows/ci-security.yml`（与其他检查并行，每周一另跑一次），
完整冒烟与性能基准在 `ci-infra.yml` 的全栈验收里。本机与 CI 用的是同一套脚本。

### 15.1 安全扫描：`make scan`

| 子命令 | 内容 | 什么时候失败 |
|---|---|---|
| `scripts/security-scan.sh deps` | pip-audit（按 `services/api/uv.lock`）与 npm audit（`apps/web`、`tests/e2e` 的锁文件），都包含开发依赖 | 有高危或严重漏洞；中低危只报告 |
| `scripts/security-scan.sh secrets` | gitleaks 扫描整个 git 历史 | 有任何发现。已知误报按指纹登记在 `.gitleaksignore`，每条写明理由 |
| `scripts/security-scan.sh images` | trivy 扫描已有修复版本的高危 / 严重漏洞 | 本仓库构建的镜像（api、web）有就失败；第三方锁定镜像只报告 |

- 需要：`deps` 要 `uv` 与 `npm`；`secrets` 与 `images` 要 Docker。`images` 要先构建镜像（`make up`），
  第三方镜像的清单取自 compose 文件，所以还要有 `.env`。
- trivy 的漏洞库缓存在 `~/.cache/trivy`（约 1 GB，`TRIVY_CACHE_DIR` 可改）；`SCAN_THIRD_PARTY=0` 只扫描本仓库的镜像。
- 出网经过 TLS 代理的环境：设置 `SSL_CERT_FILE` 指向代理的 CA，脚本会把它挂进 trivy 容器；`HTTPS_PROXY` 等变量原样传入。
- **第三方镜像有漏洞时**：修复只能来自上游的新版本。发布前对照报告查看上游是否已发布修复版本；
  升级时按项目规则重新核验版本与 digest，更新 compose 文件，再跑全部验收，不要为了让扫描变绿而跟随最新版本。
- **新增误报时**：确认它不是真实凭据（例如测试里的固定假值、第三方文档里的公开示例），
  把 gitleaks 输出的 `Fingerprint` 加进 `.gitleaksignore`，上一行写明理由。真实凭据一旦进入 git 历史就要立即作废并更换。

### 15.2 完整端到端冒烟：`make smoke-full`

先运行 `make smoke`，再用浏览器（Playwright）检查整个系统的基本可用性：

1. 登录；
2. `services/api/openapi.json` 里的全部 GET 接口逐个请求，都应返回 200（需要具体资源 ID 的少数接口由各 Sprint 验收覆盖）；
   运维接口报告的核心服务（API、Worker、PostgreSQL、Redis）都是“正常”；
3. 导航里的全部页面（`apps/web/src/app/navigation.ts`）加审计日志页逐个打开，标题正确，没有页面错误、控制台错误与 5xx；
4. 实时连接（SSE）能建立；
5. 写入往返：新建日程、首页加便签 → 可见 → 删除；
6. 登出后接口返回 401。

新增的接口和页面会自动纳入检查。需要 `tests/e2e` 的依赖与 Chromium（`cd tests/e2e && npm ci && npm run browser:install`）。

- 默认用 `tests/e2e/seed.py` 在 api 容器里新建一个临时用户（本机与 CI）。
- 在已经部署的环境里检查时，设置 `SMOKE_EMAIL` / `SMOKE_PASSWORD` 使用一个专门的冒烟账号（工作空间的 owner 或 admin）；写入的测试数据在结束时删除。
- 没有配置 Vikunja、GitHub、n8n、Prometheus 时，相关页面显示“未配置”，冒烟照样通过。

### 15.3 性能基准：`make perf`

`tests/perf/benchmark.py`（只用 Python 标准库）新建临时用户，写入 200 条任务投影、40 个本周日程与 50 条通知，
经 Caddy 测量：

- 基础 API（当前用户、工作空间、任务、本周日程、通知）：目标 P95 < 300 ms；
- 首页聚合 `/api/v1/dashboard/overview`（读本地投影，预热后）：目标 P95 < 500 ms。

每个接口先预热，再按并发 4 与 16 各发 200 次请求，输出 P50 / P95 / P99 / 最大值、错误数与吞吐的表格
（`--output report.json` 另存 JSON）。判定：没有错误，且 P95 不超过“目标 × `--tolerance`”。

- 本机固定环境用默认的 `--tolerance 1`，结果写进验收报告；CI 的 runner 性能不稳定，用 `--tolerance 2`，表格写进运行的 Summary。
- 这些是测试目标，不是承诺（规范第 12 节）。本机实测数据与未达标项的原因见上述 ADR 的第 8.6 节，Sprint 6 的验收报告会附上发布前的数据。


## 16. OpenClaw 接入入口（V2，`--profile agents`）

OpenClaw 实例在别的服务器上，**主动连回工作台**：内网模式经私网地址，外网模式经公网域名加双向证书。
设计见 `docs/v2/TRANSFORMATION_PLAN.md` 第 4.2、6 节；核验结论见
`docs/decisions/2026-10-31-v2-oc2-agent-ingress-verification.md`。

### 16.1 证书与变量

1. 准备接入入口的服务器证书，放进 `AGENT_TLS_DIR`（默认 `infra/caddy/agent-tls/`，目录已忽略，不提交）：
   - `intranet.crt` / `intranet.key`：内网入口，内部 CA 或 Tailscale 证书都可以；
   - `public.crt` / `public.key`：外网入口，公网证书（例如 certbot 申请的证书）。
2. 在 `.env` 里设置：

   | 变量 | 例子 | 说明 |
   |---|---|---|
   | `AGENT_INTRANET_HOST` / `AGENT_PUBLIC_HOST` | `ws.tailnet.example` / `agent.example.com` | 证书里的域名；外网入口开启客户端证书后 SNI 必须与 Host 一致 |
   | `AGENT_INTRANET_BIND` / `AGENT_INTRANET_PORT` | `100.64.0.5` / `8443` | 内网入口发布的地址与端口（默认 `127.0.0.1:8443`） |
   | `AGENT_PUBLIC_BIND` / `AGENT_PUBLIC_PORT` | `0.0.0.0` / `9443` | 外网入口发布的地址与端口（默认 `127.0.0.1:9443`） |
   | `AGENT_INTRANET_ALLOWED_CIDRS` | `100.64.0.0/10` | 内网入口只接受的来源（空格分隔） |
   | `AGENT_INTRANET_URL` / `AGENT_PUBLIC_URL` | `https://ws.tailnet.example:8443` / `https://agent.example.com:9443` | 写进接入包的地址；没有配置的入口不能生成该模式的接入包 |
   | `AGENT_INGRESS_NET` | 默认 `172.31.250` | `agent-ingress` 网络的网段前三段：子网 `<值>.0/29`，agent-gateway 固定在 `<值>.2`。同一台机器上有别的 Docker 网络占用了这个网段时改它；`make restore-verify` 的临时环境自动用 `172.31.251` |
   | `AGENT_INGRESS_TRUSTED_PROXIES` | 默认 `<AGENT_INGRESS_NET>.2` | API 只信任这个地址写入的入口与证书请求头；一般不用改 |

3. `docker compose up -d api`，让 api 读到新的变量。
4. `make agents-up`：
   - 先导出内部 CA 证书（`make agent-ca`，不含私钥）；
   - 再启动 `agent-gateway`。
5. 防火墙：外网入口的端口对公网开放；内网入口的端口只在私网开放。

### 16.2 接入一台 OpenClaw 服务器

1. 工作台的实例页：登记实例，选网络模式和权限，授权密钥，下载接入包（zip，凭证只出现这一次）；
2. 在 OpenClaw 服务器上解压，按里面的 `CHECKLIST.md` 设置属主、权限、插件和定时任务，然后 `docker compose up -d`；
   - 外网模式会同时启动 `tunnel` 服务：本机隧道，用接入包里的客户端证书连外网入口；
   - 内网模式、且工作台也配置了外网入口时，会同时启动 `image-proxy` 服务：本机取图代理（不带客户端证书），
     把工作台发来的图片地址（外网域名）转到内网入口（OC-3 ADR 第 15 节）；
3. 入口用私有 CA 签发的证书时：
   - 外网模式：在接入包 `compose.yaml` 的 `tunnel` 命令里加 `--cacert=<CA 证书>`；
   - 内网模式：在 `myworkspace.env` 里设置 `NODE_EXTRA_CA_CERTS=<CA 证书>`；有 `image-proxy` 时在它的命令里也加 `--cacert=<CA 证书>`。
4. 重新生成接入包会签发新凭证，旧凭证默认再保留 24 小时；客户端证书 90 天到期，到期前重新生成。

### 16.3 验收

```
python3 tests/e2e/agent_ingress_acceptance.py --server-ca <签发入口证书的 CA> \
    --intranet <内网域名>:<端口> --public <外网域名>:<端口> --connect <发布端口所在的地址>
```

覆盖内网与外网入口、双向证书、本机隧道、入口错配、吊销，以及伪造请求头。

## 17. OpenIM（V2 管理员端 IM 的后端，独立部署）

管理员在工作台里和 OpenClaw 实例聊天，消息经 OpenIM 中转。OpenIM 是**独立的 Compose 项目**（`infra/openim/compose.yaml`），
和 Vikunja 一样加入 core 创建的网络。设计见 `docs/v2/TRANSFORMATION_PLAN.md` 第 4.4、6.1 节；
补丁镜像、接入方式与实测见 `docs/decisions/2026-10-31-v2-oc3-openim-patched-image.md`（第 16 节是部署）。

### 17.1 网络与地址

| 网络 | 成员 | 用途 |
|---|---|---|
| `im`（core 创建，internal） | api、worker；OpenIM 的 openim-server（固定 `<IM_NET>.2`）与 garage | API 调 OpenIM REST、上传和下载图片；OpenIM 回调 API |
| `agent-im`（core 的 agent-gateway 创建，internal） | agent-gateway、openim-server | 接入入口把 `/im/ws` 转发到消息网关 |
| `openim`（OpenIM 项目自己的） | MongoDB、Redis、etcd、Kafka、Garage、openim-server | OpenIM 内部；不发布任何端口 |

- 浏览器和实例都不直连 OpenIM：管理员经工作台 API，实例经接入入口（`/im/ws`、`/im/api/*`、`/im/obj/m/*`，第 16 节）；
- OpenIM 的回调没有签名，所以 API 只接受来自 `<IM_NET>.2` 的回调，地址里还有随机口令（访问日志里记成 `***`）；
- **before 回调失败即拒**：工作台 API 不可用时，IM 消息直接发送失败（OpenIM 返回 90004），这是有意的（契约第 10 节）。

### 17.2 部署步骤

1. `make env`：在 `.env` 里随机生成 `OPENIM_SECRET`、`OPENIM_CALLBACK_PATH_TOKEN` 和 OpenIM 各组件的密码（已有的值不改）；
2. `make openim-build`：按锁定的上游提交与补丁构建 `my-workspace/openim-server` 镜像（要 8 GB 左右的临时磁盘；
   Docker Hub 限流时 `BASE_REGISTRY=mirror.gcr.io/library make openim-build`）；
3. `make up`、`make agents-up`（第 16 节）：创建 `im` 与 `agent-im` 网络；
4. `make openim-up`：启动 OpenIM，第一次启动要 1 分钟左右（初始化 etcd、Garage、MongoDB 用户）；
5. 在 `.env` 里设置下面两项，再 `make up` 让 api、worker 生效：

   ```
   OPENIM_API_URL=http://openim-server:10002
   OPENIM_OBJECT_URL=http://garage:3900
   ```

6. 打开工作台的 IM 页面：管理员第一次打开时自动注册 IM 账号；登记的实例会自动注册并加好友（失败时 Worker 每 5 分钟重试）。

### 17.3 变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENIM_API_URL` / `OPENIM_OBJECT_URL` | 空 | 留空表示没有部署 OpenIM：IM 接口返回 503，Worker 不做 IM 对账 |
| `OPENIM_SECRET` | `make env` 生成 | OpenIM 的 share.secret；工作台用它换 imAdmin 令牌，接入网关用它校验实例的 IM 令牌。更换后所有 IM 令牌失效，实例要重启 |
| `OPENIM_CALLBACK_PATH_TOKEN` | `make env` 生成 | 回调地址 `http://api:8000/internal/openim/<口令>` 里的口令，至少 32 个字符 |
| `IM_NET` | `172.31.252` | `im` 网络的网段前三段：子网 `<值>.0/28`，openim-server 固定在 `<值>.2`。和本机别的 Docker 网络冲突时改它；`make restore-verify` 的临时环境自动用 `172.31.253`，并且不连接 OpenIM |
| `OPENIM_CALLBACK_ALLOWED_CIDRS` | `<IM_NET>.2/32` | API 只接受这些地址的回调；一般不用改 |
| `AGENT_IM_OBJECT_BASE_URL` | 自动 | 写进图片消息的取图地址前缀；默认是 `http://<外网入口域名>:18443`（接入包里本机隧道的端口），只有内网入口时是内网入口地址 |
| `OPENIM_MONGO_*`、`OPENIM_REDIS_PASSWORD`、`OPENIM_ETCD_*`、`OPENIM_KAFKA_PASSWORD`、`OPENIM_GARAGE_*`、`OPENIM_S3_*` | `make env` 生成 | 只在 OpenIM 的 Compose 里使用。MongoDB 的用户与 Garage 的访问密钥在**第一次启动时**写进数据卷，之后改 `.env` 不会生效 |

### 17.4 日常

- `make openim-logs` 看日志；`make openim-down` 停止（保留数据卷）；
- 资源：空闲时 openim-server 约 390 MiB、Kafka 约 370 MiB、MongoDB 约 190 MiB、Garage 与 etcd 各十几 MiB；
- 升级 OpenIM 只能换补丁镜像（`infra/openim/patches/`），**不要换回官方镜像**：官方镜像有越权问题（OC-0 ADR 第 4 节）。

### 17.5 备份与恢复

- **备份**：`make backup` 发现 OpenIM 在运行（项目名默认 `<COMPOSE_PROJECT_NAME 或 my-workspace>-openim`，可用 `OPENIM_PROJECT` 指定），
  就把下面三个文件放进同一个 restic 快照：
  - `openim-mongo.archive.gz`：`mongodump --archive --gzip`，只含 `openim_v3` 库；
  - `openim-garage.tar`：先 `garage meta snapshot`，再打包快照出来的元数据库、节点密钥与集群布局，以及数据块目录；
  - `openim-info.json`：备份时各集合的文档数与桶里的对象数，恢复时用来核对。
- 配置了 `OPENIM_API_URL` 但 OpenIM 没在运行时，`make backup` 照常备份工作台，并提示这次不含 OpenIM。
- **恢复演练**：`make restore-verify` 会在不联网的临时容器里恢复两份归档并核对数量，不影响正在运行的 OpenIM。
- **真实恢复**（在仓库根目录，用部署时的 `.env`）：
  1. 按第 14.5 节取出快照（目录里的 `backup/` 下有上面三个文件）；
  2. 工作台与接入入口先起来（`make up`、`make agents-up`）；
  3. `scripts/openim_backup.sh restore <目录>/backup`：只写入**全新的**数据卷——OpenIM 项目还有容器或数据卷时拒绝，
     绝不覆盖现有数据（旧数据卷确认不再需要后自己删除）；
  4. `make openim-up`，再按第 17.2 节第 5 步确认 `OPENIM_API_URL` 与 `OPENIM_OBJECT_URL`。
- **恢复后会话序号会往前跳一段**：OpenIM 在 MongoDB 里按块预留序号，当前值只缓存在 Redis。
  Redis 不恢复时，新消息从预留块的末尾继续编号（实测从 5 跳到 52）；中间的空号是 `status: 4` 的占位项，工作台的投影会跳过。
  不恢复 Redis 是有意的：Redis 与 MongoDB 不是同一时刻的快照，恢复旧的 Redis 可能让序号回退、覆盖已有消息。
- 实例的 IM 令牌在 Redis 里，恢复后会失效；实例重启后会重新领取（接入包的启动脚本每次启动都取新令牌）。

