# ADR：Sprint 0 集成——镜像、编排与对外入口

- 状态：已采纳
- 日期：2026-10-30
- 作者：claude
- 相关：协作任务 T-0006；`docs/decisions/2026-10-29-sprint-0-contracts.md` 第 5 节；`docs/DEVELOPMENT_SPEC.md` 第 9.3、11 节

## 背景

T-0004（前端）与 T-0005（后端）合并后，需要把各部分组装成一键启动的 core Compose，并满足规范的安全要求：
镜像固定版本、API 不以 root 运行、数据库与 Redis 不暴露公网、反向代理设置安全头与请求体限制。

## 决策

### 1. 镜像与版本（2026-10-30 在 Docker Hub / PyPI 查询并实际拉取、构建）

| 用途 | 镜像 / 工具 | 说明 |
|---|---|---|
| API / Worker 运行 | `python:3.12.15-slim-trixie` | 与 backend-stack ADR 的 Python 3.12 一致 |
| API 构建 | `uv==0.12.24`（pip 安装） | 0.12 系列最后一版；`uv sync --locked --no-dev`，只装生产依赖 |
| 前端构建 | `node:22.23.4-alpine3.24` | 与 codex 验证前端时使用的 Node 版本一致 |
| 前端运行 | `nginxinc/nginx-unprivileged:1.30.6-alpine3.24` | nginx 稳定分支，非 root（uid 101），监听 8080 |
| 对外入口 | `caddy:2.11.7-alpine` | 规范指定 Caddy |
| 数据库 / 缓存 | `postgres:16.15-alpine3.24`、`redis:7.4.12-alpine3.21` | 与 CI service container 相同 |

自建镜像标记为 `my-workspace-api:local`、`my-workspace-web:local`，只在本机构建使用，不推送到镜像仓库。

### 2. 编排与网络

- 6 个服务：web、api、worker、postgres、redis、caddy；除 caddy 外都有健康检查，`make up` 使用 `--wait` 等待全部健康。
- `backend` 网络设为 internal（不通外网），postgres、redis、worker 只在其中；api 同时接入 `frontend`，供 caddy 访问。
  Sprint 3 起 Worker 需要访问外部 Connector 时，再把它接入有出口的网络。
- 对外只发布 caddy 的端口，默认绑定 `127.0.0.1:8080`（`HTTP_BIND`、`HTTP_PORT` 可配置）。
- api、worker、web：只读根文件系统 + `/tmp` tmpfs、`cap_drop: ALL`、`no-new-privileges`。
  caddy 需要保留 `NET_BIND_SERVICE`：官方镜像给二进制设置了该文件能力，缺少时无法执行。
- 日志使用 json-file 驱动，单文件 10 MB、保留 5 个。

### 3. 对外入口（`infra/caddy/Caddyfile`）

- `/api/*`、`/healthz` → api；其余 → web。
- `/readyz` 只转发内网来源（`remote_ip private_ranges`），公网来源返回 404（规范第 6.1 节标注为 internal）。
- 安全响应头：CSP（`default-src 'self'`，`style-src` 允许 `'unsafe-inline'` 以兼容 Vue 的行内样式）、
  `X-Content-Type-Options`、`X-Frame-Options: DENY`、`Referrer-Policy`、`Permissions-Policy`、COOP，去掉 `Server` 头。
  已用无头 Chromium 验证首页在该 CSP 下无报错。
- 请求体上限 10 MB。HSTS 只有启用 HTTPS 后才有意义，开发环境不加，生产步骤写在 `docs/deployment.md` 第 6 节。

### 4. 配置与密钥

- `make env`（`scripts/init_env.py`）从 `.env.example` 生成 `.env`，为数据库密码、`SESSION_SECRET`、`CREDENTIAL_ENCRYPTION_KEY` 生成随机值，权限 600。
  因此仓库里没有任何可用的默认密码，全新机器上 `make up` 即可启动。
- API / Worker 的 `DATABASE_URL` 在 Compose 中由 `POSTGRES_*` 拼接，避免与数据库密码不一致。

### 5. 迁移与运维命令

- `make migrate` 在运行中的 api 容器里执行 `alembic upgrade head`；本机开发用 `make migrate-local`。
- `make down` 不删除数据卷；文档明确禁止把 `docker compose down -v` 当作常规停机。

## 后果

- CI 新增 `ci-infra`：每个 PR 都会构建镜像并完整启动一次（约几分钟）。
- 升级任何镜像都要同时修改 Dockerfile / `compose.yaml`、本 ADR 的版本表，必要时同步 `ci-api.yml`。
- 在需要经代理才能出网的构建环境中（例如部分云端开发环境），本地 `docker build` 需要额外传入代理与 CA；这属于环境配置，不写进仓库的 Dockerfile。
