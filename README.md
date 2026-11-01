# WorkspaceCore

this is a personal workspace

My Workspace：自托管的混合型个人工作台（统一首页、任务、文档入口、日历、GitHub、运维、通知、搜索、自动化、AI 辅助）。
设计与范围见 [`docs/DEVELOPMENT_SPEC.md`](docs/DEVELOPMENT_SPEC.md)。

> 当前进度：Sprint 0 ~ 5 已验收，验收记录见 [`docs/acceptance/`](docs/acceptance/)：
> - 工程骨架；认证、工作空间、设置与审计；
> - 可配置首页；Vikunja 任务；
> - 知识库收藏、日历、GitHub 与跨资源搜索；
> - 运维、通知与 n8n 自动化。
>
> Sprint 6 已完成：备份恢复、安全扫描、性能基准与发布文档，以及 V2 的 AI 部分——OpenClaw 多实例接入、基于 OpenIM 的管理员端 IM、集中密钥库与写操作审批（可选部署，见 `docs/deployment.md` 第 16 ~ 18 节；验收见 `docs/acceptance/sprint-6.md`）。

## 快速开始

需要：Docker（含 Compose v2）、GNU Make、Python 3。

```bash
make up        # 首次自动生成 .env（随机密码），构建并启动全部服务
make migrate   # 首次启动后执行数据库迁移
make smoke     # 检查健康、就绪、前端页面与安全响应头
```

打开 http://localhost:8080 。第一次使用需要创建管理员，安装凭据用 `make setup-token` 查看（见 [`docs/deployment.md`](docs/deployment.md) 第 1 节）。停止服务用 `make down`（保留数据）。`make help` 列出全部命令。

| 文档 | 内容 |
|---|---|
| [`docs/deployment.md`](docs/deployment.md) | 运行手册：部署、配置、升级与回滚、Vikunja / GitHub / 监控 / n8n 接入、备份恢复、扫描与基准 |
| [`docs/versions.md`](docs/versions.md) | 所有镜像与关键依赖的锁定版本（tag + digest）、许可证与核验来源 |
| [`docs/release-checklist.md`](docs/release-checklist.md) | 发布前逐项检查 |
| [`docs/threat-model.md`](docs/threat-model.md) | 风险与当前的防护措施 |
| [`docs/api-contract.md`](docs/api-contract.md)、[`docs/data-dictionary.md`](docs/data-dictionary.md) | 接口约定与数据库对象 |
| [`docs/decisions/`](docs/decisions/) | 技术决策与第三方版本核验（ADR） |

常用的运维命令：`make backup`（加密备份）、`make restore-verify`（隔离环境恢复演练）、`make smoke-full`（完整端到端冒烟）、`make scan`（安全扫描）、`make perf`（性能基准）。

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
| `infra/caddy/`、`compose.yaml` | 统一入口与 core 服务编排（可选监控：`--profile monitoring`） |
| `infra/vikunja/`、`infra/n8n/`、`infra/monitoring/` | 独立部署的 Vikunja、n8n 与监控配置 |
| `scripts/` | 冒烟、备份恢复、安全扫描等脚本 |
| `tests/e2e/`、`tests/perf/` | 全栈验收、浏览器验收、完整冒烟与性能基准 |
| `docs/` | 规范、任务清单、工程文档、技术决策（`docs/decisions/`） |
| `.collab/`、`scripts/collab*.py` | Claude 与 Codex 的协作状态与工具 |

## 协作方式

本仓库由 Claude 和 Codex 两个 AI 代理协作开发，进度和交接都记录在仓库里（2026-10-31 起 Codex 暂停，全部任务由 Claude 独立完成，见 `.collab/decisions/D-0007-claude-solo.md`）：

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
