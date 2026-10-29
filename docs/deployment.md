# 部署与运行手册

> 状态：Sprint 0。Docker Compose 一键启动、备份与恢复尚未实现：Compose 在 T-0006 完成，备份恢复计划在 S6-04。
> 本文目前只覆盖本机开发运行。

## 本机运行后端

前提：Python 3.12 + uv，PostgreSQL 16，Redis 7。

1. 准备数据库（数据库 owner 即可，迁移不需要超级用户）：

   ```sql
   CREATE ROLE workspace LOGIN PASSWORD '<本地密码>';
   CREATE DATABASE workspace OWNER workspace;
   ```

2. 设置环境变量并迁移：

   ```bash
   export DATABASE_URL=postgresql+asyncpg://workspace:<本地密码>@localhost:5432/workspace
   export REDIS_URL=redis://localhost:6379/0
   make setup
   make migrate
   ```

3. 启动：

   ```bash
   make dev-api                       # API，http://127.0.0.1:8000
   cd services/api && uv run arq app.jobs.worker.WorkerSettings \
       --custom-log-dict app.jobs.worker.LOG_CONFIG   # Worker
   ```

4. 检查：

   | 检查 | 预期 |
   |---|---|
   | `curl localhost:8000/healthz` | `200 {"status":"ok"}` |
   | `curl localhost:8000/readyz` | `200`，`checks` 全为 `ok`；数据库或 Redis 不可用时 `503` |
   | `cd services/api && uv run arq app.jobs.worker.WorkerSettings --check` | Worker 运行中时退出码 0 |

## 环境变量

| 变量 | 说明 | 默认值（仅本机开发） |
|---|---|---|
| `APP_ENV` | `development` / `test` / `production`；production 关闭 API 文档页 | `development` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| `DATABASE_URL` | `postgresql+asyncpg://...` | `postgresql+asyncpg://workspace@localhost:5432/workspace` |
| `REDIS_URL` | `redis://...` | `redis://localhost:6379/0` |
| `READINESS_TIMEOUT_SECONDS` | `/readyz` 每项检查的超时 | `2` |

连接串可能带密码，程序内部以 `SecretStr` 保存，不会出现在日志里。

## 停机

停止服务时不要删除数据卷；`make down`（T-0006 完成后可用）只停止容器，保留数据。
