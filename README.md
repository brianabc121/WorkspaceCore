# WorkspaceCore

this is a personal workspace

My Workspace：自托管的混合型个人工作台（统一首页、任务、文档入口、日历、GitHub、运维、通知、搜索、自动化、AI 辅助）。
设计与范围见 [`docs/DEVELOPMENT_SPEC.md`](docs/DEVELOPMENT_SPEC.md)。

> 当前进度：Sprint 0（工程骨架）已完成，验收记录见 [`docs/acceptance/sprint-0.md`](docs/acceptance/sprint-0.md)；Sprint 1（认证、工作空间、设置、审计）进行中。

## 快速开始

需要：Docker（含 Compose v2）、GNU Make、Python 3。

```bash
make up        # 首次自动生成 .env（随机密码），构建并启动全部服务
make migrate   # 首次启动后执行数据库迁移
make smoke     # 检查健康、就绪、前端页面与安全响应头
```

打开 http://localhost:8080 。第一次使用需要创建管理员，安装凭据用 `make setup-token` 查看（见 [`docs/deployment.md`](docs/deployment.md) 第 1 节）。停止服务用 `make down`（保留数据）。完整运行手册见 [`docs/deployment.md`](docs/deployment.md)，`make help` 列出全部命令。

## 本机开发

需要：Python 3.12 + [uv](https://docs.astral.sh/uv/)、Node.js 22、PostgreSQL 16、Redis 7。

```bash
make setup         # 生成 .env、安装前后端依赖
export DATABASE_URL=postgresql+asyncpg://workspace:<密码>@localhost:5432/workspace
export REDIS_URL=redis://localhost:6379/0
export PUBLIC_BASE_URL=http://localhost:5173 SETUP_TOKEN=$(make -s setup-token)
make migrate-local # 迁移本机数据库
make dev           # API（:8000）+ 前端（:5173，/api 等路径代理到 API）
make lint          # ruff、mypy、ESLint、vue-tsc
make test          # pytest、Vitest
```

设置 `TEST_DATABASE_URL`（有建库权限）和 `TEST_REDIS_URL` 后，`make test` 还会运行连接真实 PostgreSQL / Redis 的集成测试。

## 目录结构

| 路径 | 内容 |
|---|---|
| `services/api/` | FastAPI 后端（BFF）、Alembic 迁移、ARQ Worker |
| `apps/web/` | Vue 3 前端 |
| `infra/caddy/`、`compose.yaml` | 统一入口与 core 服务编排 |
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
