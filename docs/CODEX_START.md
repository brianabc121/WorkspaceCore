# 给 Codex 的第一条开发指令

> 2026-10-29 按 Claude / Codex 分工（`.collab/decisions/D-0003-module-ownership.md`）调整：本轮 Codex 只负责 Sprint 0 的**前端**部分，
> 后端、数据库迁移、Compose、根目录文件和 Sprint 验收由 Claude 负责。调整前的原文见 git 历史（提交 caf32fc）。

请担任 My Workspace 项目的**前端**实施工程师。先阅读 `AGENTS.md`、`docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md`、
`docs/decisions/2026-10-29-sprint-0-contracts.md`（前后端接口契约），然后检查当前仓库文件和 `git status`。不要覆盖用户已修改的文件。

**本轮只实现 Sprint 0 的前端部分（S0-02，协作任务 T-0004）**，禁止提前实现 S1–S6，也不要未经许可连接现有生产数据库、Vikunja、AFFiNE 或其他账号。

具体完成以下内容：
1. 在 `apps/web` 创建 Vue 3 + TypeScript + Vite 前端（Vue Router、Pinia、TanStack Vue Query、Tailwind），包含基础导航和首页占位组件，支持亮 / 暗主题。
2. 配置 ESLint、vue-tsc、Vitest；`package.json` 提供契约要求的 scripts（`dev`、`build`、`lint`、`typecheck`、`test`），提交 `package-lock.json`。
3. 新建 `.github/workflows/ci-web.yml`，在 PR 和 main 上运行 lint、typecheck、test、build。
4. 实际运行 `npm ci`、`npm run lint`、`npm run typecheck`、`npm run test`、`npm run build`；若工具或环境不支持（例如无法联网安装依赖），报告实际原因，不得捏造运行成功。

不在本轮范围（由 Claude 负责）：`services/api`、`infra/`、`compose*.yaml`、根目录 `Makefile` / `README.md` / `.env.example` / `.gitignore`、`apps/web/Dockerfile`。

限制：不写假集成数据，不假设外部 API 的字段或签名，不提交密钥，不挂载 Docker Socket，不实现无审批的服务器命令执行。任何不确定的版本/接口，请记录在 `docs/decisions` 而非猜测。

完成后按 `AGENTS.md` 格式报告：实际修改文件、完成任务编号、运行过的命令和结果、未通过项、已知风险，以及 Sprint 1 的建议步骤。**未经用户批准，不进入 Sprint 1。**

---

## 协作流程

本仓库按 `AGENTS.md` 第 1–7 节的协作协议与 Claude 协作。本轮对应的协作任务是 **T-0004**（`.collab/tasks/T-0004-sprint-0-frontend.md`）：

1. 开工先运行 `python3 scripts/collab.py inbox --as codex --no-fetch`，再 `python3 scripts/collab.py start T-0004 --as codex`。
2. 只改 `apps/web/**` 和 `.github/workflows/ci-web.yml`；不要勾选 `docs/TASKS.md`（由 Sprint 验收任务 T-0006 统一勾选）。
3. 收工：`python3 scripts/collab.py set T-0004 --as codex --status done -m "..."`（没做完就按 AGENTS.md 第 5 节写日志或交接），
   `python3 scripts/collab.py check` 通过后产出 PR，标题 `[T-0004] Sprint 0 前端工程骨架`。
