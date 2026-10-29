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

---

## 协作流程（由 claude 追加，上面的原文未改动）

本仓库按 `AGENTS.md` 第 1–7 节的协作协议与 Claude 协作。本轮对应的协作任务是 **T-0004**（`.collab/tasks/T-0004-sprint-0.md`）：

1. 开工先运行 `python3 scripts/collab.py inbox --as codex --no-fetch`，再 `python3 scripts/collab.py start T-0004 --as codex`。
2. 保留已有的 `.github/workflows/collab*.yml`、`scripts/collab*.py`；`.gitignore`、`README.md` 在现有内容上扩展。
3. 收工：在 `docs/TASKS.md` 勾选完成的 S0 条目，`set T-0004 --as codex --status done -m "..."`（没做完就按 AGENTS.md 第 5 节写日志或交接），
   `python3 scripts/collab.py check` 通过后产出 PR，标题 `[T-0004] Sprint 0 工程骨架`。
