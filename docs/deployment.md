# 部署与运行手册

> 状态：Sprint 0。core Compose 已可一键启动；备份与恢复（`make backup` / `make restore-verify`）计划在 S6-04 实现，目前会明确报“尚未实现”。

## 1. 一键启动（推荐）

前提：Docker（含 Compose v2）、GNU Make、Python 3（只用来生成 `.env`）。

```bash
make up        # 首次会自动生成 .env（随机密码），构建镜像并等待所有服务健康
make migrate   # 首次启动后执行：把数据库迁移到最新版本
make smoke     # 经 Caddy 检查健康、就绪、前端页面与安全响应头
```

打开 http://127.0.0.1:8080 即可看到首页。

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
| caddy | `caddy:2.11.7-alpine` | 统一入口：路由、安全响应头、请求体限制 | frontend | `127.0.0.1:8080`（可配置） |
| web | `my-workspace-web:local`（`apps/web/Dockerfile`） | 前端静态文件（nginx，非 root） | frontend | 无 |
| api | `my-workspace-api:local`（`services/api/Dockerfile`） | FastAPI BFF（非 root） | backend + frontend | 无 |
| worker | 同 api 镜像 | ARQ 后台作业 | backend | 无 |
| postgres | `postgres:16.15-alpine3.24` | 数据库 | backend | 无 |
| redis | `redis:7.4.12-alpine3.21` | 缓存 / 作业队列 | backend | 无 |

- `backend` 是内部网络（不通外网）；数据库和 Redis 只在这个网络里，宿主机和外网都连不到。
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
| `SESSION_SECRET` / `CREDENTIAL_ENCRYPTION_KEY` | Sprint 1 起使用 | 随机 |

数据库密码请只用字母和数字（`make env` 生成的就是），否则需要在连接串中做 URL 编码。

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
- **`APP_ENV=production`**：关闭 `/api/v1/docs` 与 OpenAPI。
- 备份恢复尚未实现（S6-04）；在此之前请自行用 `pg_dump` 做数据库备份。

## 7. 本机直接运行（开发）

不使用 Docker 时，需要本机的 Python 3.12 + uv、Node.js 22、PostgreSQL 16、Redis 7：

```bash
make setup                              # 生成 .env 并安装前后端依赖
export DATABASE_URL=postgresql+asyncpg://workspace:<密码>@localhost:5432/workspace
export REDIS_URL=redis://localhost:6379/0
make migrate-local                      # 迁移本机数据库
make dev                                # API :8000 + 前端 :5173（/api 等路径代理到 API）
```

本机数据库只需要数据库 owner 权限，迁移不需要超级用户：

```sql
CREATE ROLE workspace LOGIN PASSWORD '<本地密码>';
CREATE DATABASE workspace OWNER workspace;
```
