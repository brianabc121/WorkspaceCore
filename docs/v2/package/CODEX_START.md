# 给 Codex 的第一条开发指令

请担任 My Workspace 项目的实施工程师。先阅读 `AGENTS.md`、`docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md`，然后检查当前仓库文件和 `git status`。不要覆盖用户已修改的文件。

**本轮只实现 Sprint 0（工程骨架）**，禁止提前实现 S1–S6，也不要未经许可连接现有生产数据库、Vikunja、AFFiNE 或其他账号。

具体完成以下内容：
1. 创建 Vue 3 + TypeScript + Vite 前端（Vue Router、Pinia、TanStack Vue Query、Tailwind），包含基础导航和首页占位组件。
2. 创建 Python FastAPI + Pydantic 2 + SQLAlchemy 2 后端，提供 `/healthz` 与 `/readyz`，带结构化日志与测试。
3. 配置 PostgreSQL、Redis、Caddy、Web、API、Worker 的基础 Docker Compose；数据库/Redis 不暴露公网端口；不要使用生产浮动 `latest` 镜像。
4. 建立 Alembic、`.env.example`（仅占位符）、Makefile、README、GitHub Actions CI 的基础结构。
5. 跑通 `make up`、`make migrate`、`make lint`、`make test`、前端 build、`docker compose config`；若工具或环境不支持，报告实际原因，不得捏造运行成功。

限制：不写假集成数据，不假设外部 API 的字段或签名，不提交密钥，不挂载 Docker Socket，不实现无审批的服务器命令执行。任何不确定的版本/接口，请记录在 `docs/decisions` 而非猜测。

完成后按 `AGENTS.md` 格式报告：实际修改文件、完成任务编号、运行过的命令和结果、未通过项、已知风险，以及 Sprint 1 的建议步骤。**未经用户批准，不进入 Sprint 1。**
