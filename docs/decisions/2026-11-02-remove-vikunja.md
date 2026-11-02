# 2026-11-02 移除 Vikunja，项目与任务由工作台自己管理（T-0082）

- 状态：accepted（人类 2026-11-02 在对话中决定；技术细节由 claude 决定）
- 取代：`2026-10-30-vikunja-2.7.1-verification.md`、`2026-10-30-sprint-3-tasks-contract.md`
  （保留为历史记录）；`2026-11-02-deploy-connect.md` 里 Vikunja 的部分；`2026-11-02-app-logins.md` 里 Vikunja 的部分
- 关联：迁移 `0020`、`services/api/app/domain/tasks.py`、`apps/web/src/features/tasks/`、`scripts/deploy.sh`

## 1. 背景

人类要求：「彻底移除掉 Vikunja，改为通过 workspace 进行项目创建、管理。只需要做单人版本即可」。
在对话里的三个选择：

| 问题 | 选择 |
|---|---|
| 已同步到工作台的项目、任务 | 一次性转成工作台自己的数据（ID 不变，收藏关联、搜索照常） |
| 多人协作（负责人、指派、评论、按项目共享） | 去掉，只做单人版；角色不变（viewer 只读，member 及以上可写） |
| 服务器上已经部署的 Vikunja | `deploy.sh upgrade` 停掉并删除容器与对外站点，**保留数据卷**，打印手动删除的命令 |

这推翻了规范第 4 节和 AGENTS.md 第 8 节的“Vikunja 是任务主源”，两处文字在同一个 PR 里更新。

## 2. 数据（迁移 0020）

1. **新表** `workspace.projects`（标题、说明、是否归档、整数 version、创建 / 更新时间、创建者）与
   `workspace.tasks`（项目、标题、说明、是否完成与完成时间、优先级 0–5、截止时间、标签 `text[]`、整数 version）。
   任务经复合外键 `(workspace_id, project_id)` 指向项目：任务不可能挂到别的工作空间的项目上；删除项目时级联删除任务。
2. **转换**：`external_resources` 里 provider = vikunja、未删除的项目 / 任务原样插入新表，**主键不变**，
   所以收藏的关联、AI 审批记录里的 `task_id`、前端书签链接都照旧有效。找不到所属项目的任务放进每个工作空间
   一个「未分组任务」项目。优先级超出 0–5 的截到范围内；标签从投影的 JSON 取。
3. **收藏关联**：`resource_links.target_resource_id` 不再是指向 `external_resources` 的外键，而是项目或任务的 ID；
   迁移删掉已经指向不存在对象的关联，之后由 projects / tasks 上的触发器在删除时清理（与之前的 tombstone 效果相同）。
4. **删除 Vikunja**：删掉 provider = vikunja 的集成（级联删除投影、凭证、同步游标）、`webhook_inbox` 表、
   `integrations.webhook_secret_ref`、保存的 Vikunja 登录账号（`app_logins` 只允许 `n8n`）。
5. **搜索**：项目与任务由各自的触发器写进 `search_documents`（provider = `local`，没有外部链接，任务的 context 是项目名，
   项目改名时同步更新）；`search_index_external` 只处理 GitHub；迁移最后整体重建一次索引。
6. **降级**只恢复表结构（webhook_inbox、外键、列、旧的搜索函数），不恢复数据：工作台的项目、任务会被删除。
   之前的数据仍在 Vikunja 自己的数据卷里（升级时保留）。

迁移删除了已有数据（Vikunja 集成与 Webhook 收件箱），按 D-0004 第 3 节，这个 PR 由人类确认后合并。

## 3. 接口

- 项目：`GET /projects`（默认不含已归档，`include_archived=true` 包含；带未完成 / 已完成任务数；`q` 按标题搜索）、
  `POST /projects`、`GET /projects/{id}`、`PATCH /projects/{id}`（改名、说明、归档 / 取消归档）、
  `DELETE /projects/{id}?version=`（连带删除任务，审计记录删除的任务数）、`GET /projects/progress`（首页组件）。
  写接口用整数 `version` 做乐观并发，不一致返回 409 `VERSION_CONFLICT`（`details.current` 是最新的项目），
  不需要 Idempotency-Key（新建项目重复提交的后果只是多一个空项目，可以删）。
- 任务：路径与 Sprint 3 相同；写接口仍然**必须带 Idempotency-Key**（前端的“查询原请求结果”、AI 审批执行都依赖它），
  `version` 仍是不透明字符串（内部是整数）。新增 `labels`。去掉 `integration_id`、`external_id`、`identifier`、
  `source_url`、`source_updated_at`、`synced_at`；列表不再带 `sync`。
- 幂等在**同一个事务**里完成：检查键 → 执行写入（保存点）→ 记下结果。并发的同一个请求在唯一约束上失败，
  返回 409 `IDEMPOTENCY_IN_PROGRESS`（`Retry-After: 1`），它的写入随事务回滚；确定的失败（404、409）也记下来，
  重放时返回同样的错误。T-0082 之前留下的“处理中”记录（Vikunja 写入结果未知）视为作废，按新请求执行。
- 删除：`POST /webhooks/vikunja/{id}`、Vikunja 集成的新建 / 地址修改（集成只剩 GitHub，`provider` 只能是 `github`，
  `IntegrationCheck.name` 去掉 `tasks_read`）、`connect vikunja` / `relink` 命令。
- 首页任务模块总是 `ok`（本地数据），新工作空间的汇总是 0 而不是 null。
- 权限不变：工作空间范围 + 角色（viewer 读，member 及以上写），在后端校验。

## 4. 前端

- 「项目」页：新建、改名 / 改说明、归档（默认隐藏，可勾选显示）/ 恢复、删除（二次确认，提示会删除几个任务）；
  版本冲突提示重新加载；viewer 只读。顶栏「创建」菜单加「新建项目」。
- 「任务」页与任务抽屉：显示所属项目与标签，编辑标签（逗号或顿号分隔）；`?project_id=` 筛选项目、`?task=` 打开任务
  （项目页、首页项目进度、搜索结果、日历截止日、收藏关联都跳到这里）。
- 去掉所有 Vikunja 文案、Vikunja 连接表单、项目页的 Vikunja 登录账号卡片、“在 Vikunja 中打开”链接。

## 5. 部署

- `deploy.sh connect` 不再部署、接入 Vikunja；对外子域名只剩 n8n。
- `deploy.sh upgrade` 在迁移之后调用 `retire_vikunja`：按 Compose 项目标签（`<core>-vikunja`）找到以前的 Vikunja 容器，
  `docker rm -f` 并删除它的网络；删除 `infra/caddy/apps-enabled/vikunja.caddy` 并重启 caddy；清空 `VIKUNJA_DOMAIN`；
  **不删数据卷**，打印 `docker volume rm …` 的命令由管理员确认后手动执行。不依赖 `infra/vikunja/compose.yaml`（已删除）。
- 删除 `infra/vikunja/`、Makefile 的 `vikunja-*` 目标、`.env.example` 的 `VIKUNJA_*` 与 `WEBHOOK_BASE_URL`；
  `INTEGRATION_ALLOWED_HOSTS` 默认只有 `n8n`。已有服务器 `.env` 里的旧变量不再被读取，可以留着。
- `deploy.sh reset` 仍然清理 `<core>-vikunja` 项目（以前部署过的）。

## 6. 验收与测试

- 后端：`tests/test_tasks_api.py` 重写（项目增删改查、版本冲突、任务的幂等与并发、标签、完成时间、权限、
  工作空间隔离、首页）；搜索、收藏关联、AI 工具、MCP、OC-6 安全测试改用本地项目与任务；契约测试确认 Vikunja 端点已删除。
- 迁移：在有 Vikunja 投影数据的库上升级（转换、关联、搜索）再降级再升级。
- 前端：项目页测试（新建、改名、归档、删除、冲突、viewer、创建菜单）、任务页的项目筛选与定位、标签。
- CI：`tests/e2e/sprint3_acceptance.py` / `sprint3_browser.cjs` 换成本地项目与任务的全栈验收，不再启动 Vikunja。
