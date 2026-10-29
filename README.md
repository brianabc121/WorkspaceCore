# WorkspaceCore

this is a personal workspace

My Workspace：自托管的混合型个人工作台（统一首页、任务、文档入口、日历、GitHub、运维、通知、搜索、自动化、AI 辅助）。
设计与范围见 [`docs/DEVELOPMENT_SPEC.md`](docs/DEVELOPMENT_SPEC.md)。

> 当前进度：Sprint 0（工程骨架）。后端最小服务与数据库迁移已就绪；前端骨架（T-0004）与 Docker Compose（T-0006）进行中。

## 快速开始（本机开发）

需要：Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node.js 22、PostgreSQL 16、Redis 7。

```bash
make setup     # 复制 .env、安装依赖
make help      # 列出全部命令
```

后端在本机直接运行时，用环境变量指向本机数据库（默认值为 `localhost` 上无密码的 `workspace` 库）：

```bash
export DATABASE_URL=postgresql+asyncpg://workspace:<密码>@localhost:5432/workspace
export REDIS_URL=redis://localhost:6379/0
make migrate   # 数据库迁移到最新版本
make dev       # 启动 API（:8000），前端存在时同时启动（:5173）
```

验证：`curl localhost:8000/healthz` 返回 `{"status":"ok"}`；`curl localhost:8000/readyz` 检查数据库与 Redis。

质量检查：

```bash
make lint      # ruff、mypy（以及前端 lint / typecheck）
make test      # pytest（以及前端测试）
```

设置 `TEST_DATABASE_URL`（有建库权限）和 `TEST_REDIS_URL` 后，`make test` 还会运行连接真实 PostgreSQL / Redis 的集成测试。
一键启动全部服务的 `make up` 将在 Sprint 0 集成任务（T-0006）中完成。

## 目录结构

| 路径 | 内容 |
|---|---|
| `services/api/` | FastAPI 后端（BFF）、Alembic 迁移、ARQ Worker |
| `apps/web/` | Vue 3 前端（T-0004） |
| `docs/` | 规范、任务清单、工程文档、技术决策（`docs/decisions/`） |
| `.collab/`、`scripts/collab*.py` | Claude 与 Codex 的协作状态与工具 |

## 协作方式

本仓库由 Claude 和 Codex 两个 AI 代理协作开发，进度和交接都记录在仓库里：

- 代理规则：[`docs/AGENTS.md`](docs/AGENTS.md)
- 方案说明与操作手册：[`.collab/README.md`](.collab/README.md)
- 查看进度：GitHub 上的「📋 协作看板」issue（CI 自动更新），或 `python3 scripts/collab.py status`

## 平台设计文档

平台的设计文档统一存放在 [`docs/`](docs/) 目录下，包含以下五个文件，供平台开发、任务规划和协作时参考：

| 文档 | 说明 |
| --- | --- |
| [DEVELOPMENT_SPEC.md](docs/DEVELOPMENT_SPEC.md) | 平台开发设计规范（Markdown 版） |
| [DEVELOPMENT_SPEC.docx](docs/DEVELOPMENT_SPEC.docx) | 平台开发设计规范（Word 版） |
| [AGENTS.md](docs/AGENTS.md) | 开发协作与代理工作说明 |
| [TASKS.md](docs/TASKS.md) | 开发任务清单 |
| [CODEX_START.md](docs/CODEX_START.md) | Codex 开发启动说明 |
