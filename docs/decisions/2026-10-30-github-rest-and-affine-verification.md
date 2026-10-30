# Sprint 4 版本核验：GitHub REST API（2026-03-10）与 AFFiNE 0.27.4 深链接（S4-01、S4-04）

- 状态：accepted
- 日期：2026-10-30
- 作者：claude（协作任务 T-0025）
- 适用：Sprint 4 的接口契约（T-0026）、后端（T-0027、T-0028、T-0029）、前端（T-0030、T-0031）与验收（T-0032）

## 1. 结论

| 项目 | 结论 |
|---|---|
| GitHub API 版本 | REST API **`2026-03-10`**，每个请求都带 `X-GitHub-Api-Version: 2026-03-10` 和 `Accept: application/vnd.github+json` |
| GitHub 范围 | V1 只支持 github.com（`https://api.github.com`）；GitHub Enterprise Server 未核验，不支持 |
| GitHub 认证 | **细粒度 Personal Access Token**（`github_pat_` 前缀），只选需要的仓库；只读权限 Metadata、Issues、Pull requests、Actions（第 3 节）；V1 不用 GitHub App |
| GitHub 同步 | 只读、轮询：Worker 串行请求，用 `ETag` 条件请求（304 不消耗额度，实测）；V1 **不启用 GitHub Webhook**（第 6 节） |
| GitHub 限流 | 主限流与次级限流都可能返回 **403 或 429**，按第 5 节的顺序退避；限流期间返回明确的限流状态，不把空列表当正常（A17） |
| AFFiNE 版本 | **0.27.4**（2026-08-18 发布，核验时的最新稳定版），镜像 `ghcr.io/toeverything/affine:0.27.4@sha256:b649f5ce2384ffdf13c23bccf81d759e15973d59d0b0058af65d895a84373099`（与 `stable` 相同） |
| AFFiNE 用法 | V1 **只保存深链接与手工元数据**，工作台不向 AFFiNE 发任何请求、不读文档正文；深链接按第 8.3 节校验 |
| AFFiNE 部署 | 本仓库**不提供** `infra/affine/`：V1 不需要运行 AFFiNE，官方 Compose 的数据库是 `trust` 认证、镜像未锁定（第 9 节） |

## 2. GitHub REST API 版本

- 当前支持的版本（实测与官方数据一致）：`2026-03-10`（尚未安排停止支持）和 `2022-11-28`（**2028-03-10 停止支持**）。
  - 请求一个不存在的版本，实测返回 `400`：`"The following versions are currently supported: "2026-03-10" (most recent) and "2022-11-28"."`。
  - 文档说明，已停止支持的版本返回 `410 Gone`。
  - 不带版本头时默认用 `2022-11-28`（实测响应头 `X-GitHub-Api-Version-Selected: 2022-11-28`）。
- 新版本发布后，旧版本还会支持 24 个月。版本临近停止支持时，响应会带 `Deprecation` / `Sunset` 头。
  Connector 收到这两个头时记一条 warning 日志，提醒升级。
- 选 `2026-03-10` 的理由：支持期最长。它的破坏性变更里与本项目有关的：
  - 去掉了 Issue / PR 的单数字段 `assignee`，只保留 `assignees`；
  - PR 去掉了 `merge_commit_sha`；
  - 仓库去掉了 `has_downloads`；
  - `GET /rate_limit` 去掉了 `rate`，改读 `resources.core`。

  实测 2026-03-10 的响应里确实没有 `assignee` 和 `merge_commit_sha`，2022-11-28 的有。
- 实际 schema 以官方 OpenAPI 描述为准：`github/rest-api-description` 的 `descriptions/api.github.com/api.github.com.2026-03-10.json`。
  - 核验时读取的提交是 `320307e`，文件 SHA-256 为 `d73b713f…35eb`，约 13.8 MB，不存进仓库。
  - T-0028 写契约测试时，从同一个文件截取用到的操作与 schema。

## 3. 认证与最小权限

- **细粒度 PAT**（文档：细粒度 PAT 以 `github_pat_` 开头，经典 PAT 以 `ghp_` 开头）。创建时：
  - **Resource owner**：选仓库所属的用户或组织。组织可以要求审批；审批前 Token 只能读公开资源（状态 `pending`）。
  - **Repository access**：选 **Only select repositories**。注意：细粒度 Token 总能只读访问 GitHub 上的所有公开仓库。
    所以工作台只同步集成配置里**明确列出的仓库**（T-0026 定字段），不去遍历所有能访问的仓库。
  - **Expiration**：可以选 1 ~ 366 天或不过期（组织可以限制最长有效期）。建议设置过期时间，并把过期时间填进集成的 `token_expires_at`
    （Sprint 3 已有该字段）。吊销：在 GitHub 设置里删除 Token，之后用它的请求按无效凭证处理（第 5 节）。
- 每个端点需要的权限：在真实 api.github.com 上读响应头 `X-Accepted-GitHub-Permissions`（2026-10-30 实测），
  并与 GitHub 官方文档数据（`github/docs` 的 `src/github-apps/data/fpt-2026-03-10/fine-grained-pat-permissions.json`）对照，两者一致。
  - 这个响应头里，逗号表示“同时需要”，分号表示“任选一组”。

| 端点（全部 GET） | 用途 | 需要的权限 |
|---|---|---|
| `/repos/{owner}/{repo}` | 测试连接、仓库信息 | Metadata: read |
| `/user/repos` | 列出可选的仓库（设置页选择用） | Metadata: read（只来自文档数据，本环境的代理只允许访问指定仓库，未实测） |
| `/repos/{owner}/{repo}/issues` | Issue 列表 | Issues: read |
| `/repos/{owner}/{repo}/pulls`、`/pulls/{pull_number}` | PR 列表与详情 | Pull requests: read（详情也接受 Contents: read 作为另一组） |
| `/repos/{owner}/{repo}/actions/runs` | 最近的 Actions 运行 | Actions: read |
| `/repos/{owner}/{repo}/actions/runs/{run_id}/jobs` | 失败运行的故障摘要（失败的 job 与 step 名称） | Actions: read |

- 所以 Token 需要的仓库权限是 **Metadata（必选，自动带上）、Issues、Pull requests、Actions，全部 Read-only**。
  不需要 Contents，不需要任何写权限，也不需要 Webhooks 权限：注册 Webhook 要 `repository_hooks` 的 write 权限，V1 不用。
- 测试连接只能**验证缺了哪些权限**，看不出多给了哪些：
  - 方法：对每个配置的仓库各调一次上表的端点，403 / 404 说明缺少对应权限，`X-Accepted-GitHub-Permissions` 指出缺的是哪一项；
  - 已读的文档里没有能列出细粒度 Token 已授权权限的接口；
  - 如果响应带非空的 `X-OAuth-Scopes`，说明这是经典 Token（文档：这个头列出 Token 已授权的 scope）。这时提示改用细粒度 Token，
    因为经典 Token 能访问用户的所有仓库。
- 不读 Actions 日志（`/logs`），因为日志里可能有密钥。故障摘要只用 job、step 的名称和结论。

## 4. 端点、分页与字段

- **分页**：用响应头 `Link`（`rel="next" / "prev" / "first" / "last"`）里的 URL 原样翻页，不自己拼 URL。
  - 实测 Issue 列表用游标（`after=…&page=2`）并改写成 `/repositories/<id>/issues`，只有 `next`；PR 和 Actions 运行用 `page=`，带 `next` / `last`。
  - 大多数端点 `per_page` 最大 100。超过时不报错，会被静默截断。同步时用 100。
- **跟随重定向**：文档要求跟随 301 / 302 / 307，例如仓库改名或转移后。Sprint 3 的 `ConnectorHttp` 用的是 `follow_redirects=False`（防 SSRF）。
  T-0028 需要只允许跟随到同一主机 `api.github.com` 的重定向，并在 301 后更新保存的仓库信息。
- **不要解析 URL 字段**：文档要求用 `number`、`id` 等字段，不要从 `html_url` 里截取。`html_url` 只用作“在 GitHub 中打开”的链接。
- **Issue 列表里包含 PR**：带 `pull_request` 键的条目是 PR，实测第 37 号 PR 出现在 `/issues` 里。同步 Issue 时跳过它们，PR 只从 `/pulls` 来。
- 各端点用到的查询参数与允许值（来自官方 OpenAPI 2026-03-10）：

| 端点 | 参数 |
|---|---|
| `/user/repos` | `visibility` = all / public / private；`affiliation`（默认 `owner,collaborator,organization_member`）；`sort` = created / updated / pushed / full_name；`direction` = asc / desc；`since`、`before` |
| `/issues` | `state` = open / closed / all（默认 open）；`sort` = created / updated / comments；`direction`；`since`（ISO 8601，只返回之后更新过的）；`labels`、`assignee`、`creator` 等 |
| `/pulls` | `state` = open / closed / all；`sort` = created / updated / popularity / long-running；`direction`；`head`、`base`。**没有 `since`** |
| `/actions/runs` | `status` = completed / action_required / cancelled / failure / neutral / skipped / stale / success / timed_out / in_progress / queued / requested / waiting / pending；`branch`、`event`、`actor`、`created`（日期范围）、`exclude_pull_requests` |
| `/actions/runs/{run_id}/jobs` | `filter` = latest / all（默认 latest） |

- 实测 2026-03-10 的响应包含以下字段（T-0026 映射时使用）：
  - Issue：`id`、`number`、`title`、`state`、`state_reason`（completed / reopened / not_planned / duplicate）、`html_url`、`user`、`labels`、`assignees`、`comments`、`created_at`、`updated_at`、`closed_at`；
  - PR：`id`、`number`、`title`、`state`、`draft`、`merged_at`、`head`、`base`、`user`、`requested_reviewers`、`labels`、`html_url`、`created_at`、`updated_at`、`closed_at`；
  - Actions 运行：`id`、`name`、`display_title`、`status`、`conclusion`、`event`、`head_branch`、`head_sha`、`run_number`、`run_attempt`、`run_started_at`、`html_url`、`workflow_id`、`actor`、`created_at`、`updated_at`，外层是 `{total_count, workflow_runs}`；
  - job 的 `conclusion`：success / failure / neutral / cancelled / skipped / timed_out / action_required。
- **外部 ID**：用 GitHub 的数字 `id`，在全 GitHub 唯一、改名不变，例如 `github:repo:<id>`、`github:issue:<id>`、`github:pull:<id>`、`github:run:<id>`。
  - 注意 PR 的 `id` 和同一个 PR 在 Issue 列表里的 `id` 不同。
  - 页面显示用 `full_name` 和 `#number`。内部仍然经 mapping，不把 ID 拼进 SQL（规范第 6 节）。
- **增量同步**的依据（细节由 T-0028 定）：
  - Issue 用 `since` 加 `sort=updated`；
  - PR 没有 `since`，用 `sort=updated&direction=desc`，翻到 `updated_at` 早于游标就停；
  - Actions 只同步最近的运行（例如每个仓库最近 N 次，或 `created` 最近几天）。
  - 文档提醒：`sort=updated` 会让列表随更新重新排序，影响 304 命中。所以“检查有没有变化”的请求要用固定参数加 `ETag`，
    “拉取变化”的请求用 `since` / `sort=updated`。
  - 删除：Issue 可能被删除、仓库可能被移出 Token 的访问范围，列表里不会出现。按 Sprint 3 的做法，用定期全量对账的差集写 tombstone。

## 5. 限流与错误 → Connector 错误分类

**额度**（文档）：
- 未认证：每个 IP 60 次 / 小时；
- PAT：每个用户 5,000 次 / 小时；
- GitHub Enterprise Cloud 组织的 GitHub App 代表用户请求时：15,000 次 / 小时；
- 同一用户的各种方式共享额度，例如 App 用掉的部分会减少 PAT 可用的部分；
- 搜索等端点更严。

**响应头**（实测与文档一致）：
- `x-ratelimit-limit` / `-remaining` / `-used` / `-reset`（UTC 纪元秒）/ `-resource`（例如 `core`）；
- 文档提醒：这些值在不同区域处理的响应之间可能不一致，不要依赖精确的剩余数。

**次级限流**（文档）：
- 同时最多 100 个请求；
- 单个端点每分钟 900 点（GET 1 点，写 5 点）；
- 每 60 秒最多 90 秒 CPU 时间；
- 写内容每分钟 80 次、每小时 500 次；
- 没有接口能查询次级限流的状态。

**条件请求**：
- 文档：带正确的 `Authorization` 时，返回 304 的条件请求不计入主限流。
- 实测（`If-None-Match` 加上次的 `ETag`）：304，`x-ratelimit-used` 前后都是 387，没有增加。

**超时**：GitHub 处理超过 10 秒会终止请求，返回服务器错误。Sprint 3 的读超时默认值是 10 秒，可以沿用。

**错误分类**：

| GitHub 响应 | 分类 | 工作台的处理 |
|---|---|---|
| 401 | `AUTH_FAILED` | 集成转为 unauthorized，提示重新连接；**不重试**（A13）。文档：凭证无效时先返回 401；短时间内多次用无效凭证请求后，该用户的**所有**认证请求（包括有效的）都会暂时被 403 拒绝 |
| 403 / 429，且 `x-ratelimit-remaining: 0` | `RATE_LIMITED`（主限流） | `x-ratelimit-reset` 之前不再请求这个集成 |
| 403 / 429，带 `retry-after` 或提示次级限流的消息 | `RATE_LIMITED`（次级限流） | 等 `retry-after` 秒；没有该头而 remaining 为 0 时等到 reset；都没有时至少等 1 分钟。连续失败就指数退避，并限制次数 |
| 其他 403 | `AUTH_FAILED`（资源级） | 缺少权限：记录 `X-Accepted-GitHub-Permissions`；不改集成状态 |
| 404 | `NOT_FOUND` | 文档：没有权限访问私有资源时也返回 404（不暴露私有仓库是否存在）。单条资源作为 tombstone 候选，仓库级 404 提示检查 Token 的仓库范围 |
| 410（版本已停止支持） | `UNAVAILABLE`（配置问题） | 记录 error 日志，提醒升级 API 版本 |
| 422 | `VALIDATION_FAILED` | 只读用法下不应出现；记录后返回 |
| 5xx、连接失败、读超时 | `UNAVAILABLE` | 读请求有限重试加退避；首页模块显示 stale / unavailable |

**其他要求**：
- 文档：被限流后继续请求可能导致集成被封禁。
- 所以每个集成的同步串行进行，沿用 Sprint 3 的同步锁 `ws:sync-lock:<id>`。
- 限流状态写进集成或游标里，页面读到的是“限流中，预计 HH:MM 恢复”，不是空列表（A17）。
- 401 的结论来自文档，本环境没有实测：本环境的出站代理会替换请求里的 `Authorization`，
  用会话自己的 GitHub 凭证（额度 15,000 / 小时，`x-ratelimit-limit` 实测值）。
  所以未认证与 PAT 的额度、401 与限流响应都以文档为准。CI 用模拟服务验证（T-0032）。

## 6. GitHub Webhook：V1 不启用

- 规范 7.3 说的是“若启用，应验证签名”。V1 不启用，理由：
  - 注册 Webhook 需要 `repository_hooks` 写权限，超出只读；
  - 需要一个 GitHub 能从公网访问的 HTTPS 地址，个人部署通常没有；
  - 轮询加 ETag 足以满足“状态投影”。
- 以后要启用时，另写决策并核验签名头（`X-Hub-Signature-256`）与投递语义，复用 Sprint 3 的 Webhook Inbox。

## 7. AFFiNE 版本与许可证

- 选 **0.27.4**：
  - 核验时 GitHub 上标为 Latest 的稳定 release；
  - 发布资产的 `Last-Modified` 为 2026-08-18；
  - tag `v0.27.4` 对应提交 `b4c8548`。
- 其他通道：`v0.x.y-beta.N` 是预发布，`v2026.MM.DD-canary.N` 是每日构建，都不使用。
- 镜像 `ghcr.io/toeverything/affine:0.27.4`（多架构索引，`sha256:b649f5ce…3099`）。
  2026-10-30 查询时 `stable` 指向同一个 digest；注意镜像标签不带 `v`。
- 0.27 的破坏性变更（release 说明）：
  - 服务端配置改为以 `config.json` 为主，部分环境变量不再可用；
  - 0.27 客户端只能连 0.27 以上的服务端。
- 许可证（tag 下的 `LICENSE` 与 `packages/backend/server/LICENSE`）：
  - `packages/backend` 与 `packages/common/native` 以外是 MIT；
  - 服务端适用 AFFiNE Enterprise Edition 许可：
    - 不属于 Community Edition 的部分在生产环境使用需要订阅；
    - 开发、测试可以免费复制和修改；
    - CE 部分与客户端资源适用 MPL 2.0。
  - 工作台只保存链接，不分发、不修改 AFFiNE；用户自行部署 AFFiNE 时自行确认许可（规范第 17 节）。

## 8. AFFiNE 深链接

### 8.1 URL 结构（tag v0.27.4 源码）

- `packages/frontend/core/src/desktop/route-paths.ts` 中的路由：
  - `WORKSPACE_ROUTE_PATH = '/workspace/:workspaceId/*'`
  - `SHARE_ROUTE_PATH = '/share/:workspaceId/:pageId'`
  - `getWorkspaceDocPath(workspaceId, docId)` 生成 `/workspace/${workspaceId}/${docId}`
- `workbench-router.ts`：工作空间下 `/:pageId` 是文档详情页；`/all`、`/collection/:id`、`/tag/:id`、`/journals`、`/trash`、`/settings`、`/chat` 是列表或功能页。
- ID 的格式：
  - 服务端工作空间的 ID 是 UUID（`schema.prisma` 中 `Workspace.id @default(uuid())`）；
  - 文档 ID 由前端用 `nanoid()` 生成（`modules/doc/stores/docs.ts`），字符集是 `A-Z a-z 0-9 _ -`。
- 文档链接可以带的查询参数（`modules/navigation/utils.ts`）：
  - `mode` = page / edgeless；
  - `blockIds`、`elementIds`（逗号分隔）；
  - `databaseId`、`databaseRowId`、`commentId`；
  - `refreshKey` 只在前端内部使用。
- 服务端配置（发布资产 `config.schema.json`）：
  - `server.externalUrl` 是对外地址，用来生成外部链接，默认 `[protocol]://[host][:port]`；
  - `server.path` 是子路径（如 `/affine`，环境变量 `AFFINE_SERVER_SUB_PATH`）；
  - 默认端口 3010。
- 部署在子路径下时，文档链接在子路径之后接 `/workspace/...`。这是由配置项推断的，没有在实例上验证（第 10 节）。

### 8.2 V1 不调用的接口

- V1 不调用 AFFiNE 的任何 HTTP / GraphQL 接口，不请求文档元数据、标题或正文，也不验证链接能否打开。
- 标题与备注由用户手工填写（规范 4.2、7.2；Sprint 4 DoD“无未授权文档正文抓取”）。
- 全文索引或原生编辑要先做 Spike（可用 API、授权范围、速率限制、许可证、版本稳定性），未通过就保持深链接（规范第 16 节）。

### 8.3 深链接的校验规则（T-0026 / T-0027 实现）

1. 工作台配置一个 AFFiNE 外部地址：应等于 AFFiNE 的 `server.externalUrl`，包括可选的子路径。配置位置由 T-0026 契约定。
   没有配置时不能添加 AFFiNE 收藏。
2. 链接的 scheme 必须是 `https`。只有外部地址本身是 `http` 时（内网开发）才接受 `http`，而且这种情况必须显式配置。
   拒绝 `javascript:`、`data:`、`file:` 以及带用户名密码的 URL。
3. scheme、主机、端口必须与外部地址完全一致；路径必须以外部地址的路径（子路径）开头。
4. 子路径之后只接受两种形式：
   - `/workspace/<workspaceId>/<docId>`
   - `/share/<workspaceId>/<docId>`

   `<workspaceId>`、`<docId>` 只允许 `[A-Za-z0-9_-]`，长度 1 ~ 64，并且 `<docId>` 不能是第 8.1 节列出的功能页名称（all、collection 等）。
5. 查询参数只保留 `mode`（page / edgeless）、`blockIds`、`elementIds`、`databaseId`、`databaseRowId`、`commentId`，其余丢弃。
   丢弃 `#` 片段。整个 URL 长度不超过 2,048。
6. 保存规范化后的 URL，以及解析出的 `workspaceId` / `docId`（用于去重：同一个工作空间里同一篇文档只收藏一次）。
7. 校验只看字符串，不发请求。所以 `169.254.169.254` 这类地址只要和配置的外部地址不一致就会被拒绝（A12）。
   配置外部地址本身时，也拒绝元数据地址与非 http(s) 的 scheme。

## 9. AFFiNE 部署：不提供 `infra/affine/`

- 官方自托管 Compose（发布资产 `docker-compose.yml`，与仓库中 `.docker/selfhost/compose.yml` 相同）有以下问题：
  - 用 `ghcr.io/toeverything/affine:stable`、`redis`（未锁版本）、`pgvector/pgvector:pg16`；
  - PostgreSQL 设置了 `POSTGRES_HOST_AUTH_METHOD: trust`，`DATABASE_URL` 不带密码；
  - 对外映射 `3010:3010`。

  这正是规范第 17 节警告的情况：不能把它放进与其他容器共享的网络。
- 本环境的出站策略拒绝 `pkg-containers.githubusercontent.com`，镜像层下载不了，无法实际运行 0.27.4 做核验。
  而 V1 根本不需要访问 AFFiNE。所以不提供 Compose，也不进 CI。
- 用户自己部署 AFFiNE 时，部署文档第 9 节写明要点：
  - 锁定 `0.27.4` 及 digest；
  - 给 PostgreSQL 设置密码，去掉 `trust`；
  - 用单独的 Compose 项目和网络，不加入工作台的 `backend` / `integrations` 网络；
  - 在 `config.json` 里设置 `server.externalUrl`；
  - 备份 PostgreSQL、`storage` 与 `config` 三者（规范 11.4）。

## 10. 未核验 / 限制

- **官方文档站无法访问**：本环境的网络策略拒绝 `docs.github.com` 与 `docs.affine.pro`。替代来源：
  - GitHub 的规则来自官方文档仓库 `github/docs` 的源文件（提交 `e6a198c`，经 raw.githubusercontent.com 读取）和官方 OpenAPI 描述；
  - AFFiNE 的结论来自 tag v0.27.4 的源码、发布资产与发布页。
- **api.github.com 只能访问本仓库**：出站代理把访问限制在本会话授权的仓库（`brianabc121/WorkspaceCore`），并替换 `Authorization`。因此：
  - `/user/repos`、未认证额度、PAT 额度、401、403 / 429 限流响应**没有实测**，以文档为准；
  - 版本头、`ETag` / 304 不计额度、`Link` 分页、`X-Accepted-GitHub-Permissions`、字段集合是在本仓库上实测的。
- AFFiNE 没有运行实例：深链接格式来自源码，没有在实例上打开验证；子路径部署时的链接形式是推断的。
  T-0032 的验收也只验证字符串校验与跳转链接，不连接 AFFiNE。
- GitHub Enterprise Server、GitHub App 安装令牌、GitHub Webhook、GraphQL API：V1 都不使用，没有核验。

## 11. 后果

- **T-0026 契约**：
  - GitHub 集成：provider `github`，`base_url` 固定为 `https://api.github.com`，浏览器链接用 `https://github.com`；
  - 配置里列出要同步的仓库；
  - 资源类型与外部 ID 按第 4 节；
  - 限流与 Token 失效的响应形状按第 5 节；
  - 首页 `github` 模块增加“限流中”的表达；
  - AFFiNE 外部地址的配置位置、收藏的字段与校验错误按第 8.3 节。
- **T-0027**：按第 8.3 节实现校验，不发任何请求。测试覆盖每条规则（包括子路径、`http` 例外、功能页名称、脚本 URL、元数据地址）。
- **T-0028**：
  - 版本头固定；重定向只跟随到 `api.github.com`；
  - 串行请求、ETag 条件请求；
  - 第 5 节的退避与错误分类；Issue 列表跳过 PR；不读 Actions 日志；
  - 用第 2 节的官方 OpenAPI 截取部分做契约测试；
  - 真实验证需要 `TEST_GITHUB_TOKEN`（细粒度 Token，按第 3 节的权限）。
- **T-0032**：CI 里用按第 4、5 节响应格式写的模拟 GitHub 服务验证 A13 / A17，并在报告里写明与真实 GitHub 的差异。

## 来源

- GitHub 实测（2026-10-30，api.github.com，仓库 `brianabc121/WorkspaceCore`）：
  - `/repos/{o}/{r}`、`/issues`、`/pulls`、`/pulls/37`、`/issues/37`、`/actions/runs`、`/actions/runs/{id}/jobs` 的响应头与字段；
  - `If-None-Match` 条件请求；
  - 版本头 `2099-01-01` 的 400 响应；
  - 不带版本头时选中的版本。
- GitHub 官方 OpenAPI：https://github.com/github/rest-api-description/blob/320307e220815d528224ca891cf8a9e3c0243678/descriptions/api.github.com/api.github.com.2026-03-10.json
- GitHub 官方文档源文件（https://github.com/github/docs ，提交 `e6a198c6ce1802c31a746ad7bdc5517c00401dfe`），
  对应 docs.github.com 上的页面（本环境打不开，供人工核对）：
  - `content/rest/about-the-rest-api/api-versions.md`、`data/tables/rest-api-versions.yml`、`data/reusables/rest-api/breaking-changes-changelog.md`
    → https://docs.github.com/en/rest/about-the-rest-api/api-versions 、https://docs.github.com/en/rest/about-the-rest-api/breaking-changes
  - `content/rest/using-the-rest-api/rate-limits-for-the-rest-api.md` 与 `data/reusables/rest-api/primary-rate-limit-*.md`、`secondary-rate-limit-rest-graphql.md`
    → https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
  - `content/rest/using-the-rest-api/best-practices-for-using-the-rest-api.md` → https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
  - `content/rest/using-the-rest-api/using-pagination-in-the-rest-api.md` → https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
  - `content/rest/using-the-rest-api/troubleshooting-the-rest-api.md`（含 `X-Accepted-GitHub-Permissions`、404 与超时）
    → https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api
  - `src/github-apps/data/fpt-2026-03-10/fine-grained-pat-permissions.json`
    → https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
  - `content/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens.md`、`about-authentication-to-github.md`（Token 前缀）、
    `content/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps.md`（`X-OAuth-Scopes`）
- AFFiNE 源码（tag v0.27.4，https://github.com/toeverything/AFFiNE/tree/v0.27.4 ）：
  - `packages/frontend/core/src/desktop/route-paths.ts`、`router.tsx`、`workbench-router.ts`
  - `packages/frontend/core/src/modules/navigation/utils.ts`、`modules/doc/stores/docs.ts`
  - `packages/backend/server/schema.prisma`、`.docker/selfhost/compose.yml`、`LICENSE`、`packages/backend/server/LICENSE`
- AFFiNE 发布：https://github.com/toeverything/AFFiNE/releases/tag/v0.27.4 （release 说明、发布资产 `docker-compose.yml` 与 `config.schema.json`）
- AFFiNE 镜像：`ghcr.io/v2/toeverything/affine/manifests/0.27.4` 与 `/manifests/stable`（2026-10-30 查询）
- AFFiNE 官方文档（本环境打不开，供人工核对）：https://docs.affine.pro/self-host-affine/ 、https://docs.affine.pro/self-host-affine/install/configuration
