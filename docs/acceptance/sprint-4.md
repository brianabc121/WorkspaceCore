# Sprint 4 验收报告

- 验收人：claude（协作任务 T-0032，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30 ~ 2026-10-31
- 结论：**Sprint 4 的 DoD 与 A10 ~ A13、A17 全部通过**。接口级（31 项）与浏览器（13 步）验收都已纳入 CI。“未运行 / 限制”逐条见第 7 节。
- 是否进入 Sprint 5：人类已在 2026-10-30 的对话中预先批准“验收通过就自动进入”（记录在 T-0032 的进度日志）。本次验收通过，按此进入 Sprint 5。

全栈环境：Compose 6 个服务，外加一个模拟 GitHub 服务（`tests/e2e/fake_github_server.py`，见第 4 节）。
模拟服务加入 integrations 网络，主机名是 `fake-github`。CI 的 `.env` 有三处改动：
`WORKSPACE_GITHUB_API_URL=http://fake-github:8080`，`INTEGRATION_ALLOWED_HOSTS=vikunja,fake-github`，`RECONCILE_INTERVAL_MINUTES=1`。

接口级脚本 `tests/e2e/sprint4_acceptance.py` 共 31 项，**已纳入 CI**（`ci-infra.yml`）。
它不要求空库：每次运行都新建工作台用户（`tests/e2e/seed.py`）。
Vikunja 的任务投影直接用 SQL 写入，模拟同步结果，用于验证关联与搜索；Vikunja 本身在 Sprint 3 已经实测。
CI 里没有真实的 GitHub Token。

## 1. Sprint 4 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 无未授权文档正文抓取 | 通过 | AFFiNE 地址配置成 `http://fake-github:8080/affine`，指向模拟服务。新建来源、多次收藏、关联、搜索之后，模拟服务没有收到任何 `/affine` 请求：收藏只做字符串校验。索引里只有标题、备注、标签与上下文（T-0029 的 pytest 覆盖） |
| 搜索结果只显示可见资源 | 通过 | A11（见第 2 节）：另一个工作空间里有同名任务也搜不到；viewer 搜不到 owner 的私有收藏，但能搜到工作空间可见的内容；非成员带别人的 `X-Workspace-Id` 搜索返回 `404`；删除和 tombstone 的资源立即从结果里消失 |
| GitHub rate-limit 有清晰降级 | 通过 | A17（见第 2 节）：被限流时列表照常显示投影；`sync` 为 `stale`，带 `reason: rate_limited` 与 `retry_at`；首页 github 模块同样标明；恢复时间之前不再请求 GitHub |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A10 源对象删除 | 搜索投影标记删除，页面无僵尸引用 | 通过。三种删除都验证了：<br>• GitHub 上删除的 Issue：手动同步（全量）后从开发者页和搜索中消失。<br>• 被收藏关联的 Vikunja 任务写 tombstone 后，收藏的 `links` 变为空，搜索也不再返回它。<br>• 删除日程后，搜索不再返回 |
| A11 搜索另一工作空间数据 | 不返回未授权结果 | 通过（见第 1 节） |
| A12 危险的 `http://169.254.169.254` 集成 URL | 被拒绝 | 通过。<br>• 作为 Vikunja 集成地址、AFFiNE 地址：都返回 `422`，响应不回显地址。<br>• 作为收藏链接（不属于已配置的 AFFiNE 地址）：`422`，`reason: host_mismatch`。<br>• 同时验证了 `javascript:` 链接（`scheme_not_allowed`）和 AFFiNE 功能页（`not_a_document`） |
| A13 失效的外部 Token | 显示重新连接，不无限重试 | 通过。模拟服务换掉 Token 后：<br>• 下一次同步得到 `401`，集成变为 `unauthorized`，开发者页 `sync.status = unauthorized`（前端据此提示重新连接）。<br>• 之后至少一轮周期对账（75 秒）里，模拟服务没有收到任何请求 |
| A17 GitHub 429 | 遵守限流和退避，不把空列表当正常 | 通过。<br>• 主限流（`403` + `x-ratelimit-remaining: 0`）：恢复时间取 `x-ratelimit-reset`。期间列表照常显示投影，`sync` 标明 `rate_limited` 与恢复时间，手动同步也不请求 GitHub（8 秒内请求数不变）。过了恢复时间，出现新的成功同步（`as_of` 前移），`sync` 回到 `ok`。<br>• 次级限流（`429` + `retry-after: 30`）：恢复时间按 `retry-after` 计算（约 30 秒），之后同样恢复 |

### 2.1 接口级的其余检查

全部通过：
- 新建 GitHub 集成返回 `201`：响应不含 Token，`base_url` 固定为 `https://api.github.com`。
- 测试连接：可达、版本、Token 三项通过，并逐个仓库检查 metadata / issues / pull_requests / actions 四项读权限。
- 首次同步：带来仓库、Issue（Issue 列表里的 PR 已排除）、PR、2 次 Actions 运行；失败的运行带故障摘要（失败的 job 与 step 名称，不读日志）。
- 收藏 AFFiNE 文档：链接规范化（保留 `mode`，去掉片段），可关联任务；私有收藏只对创建者可见。
- 日历：新建事件按 UTC 保存，区间查询返回该事件。
- 跨资源搜索：收藏、日程、GitHub 仓库与 PR 都能搜到。
- 审计日志：记录了集成、收藏、日程的写操作，且不含 Token、标题、备注与链接。

### 2.2 浏览器（`tests/e2e/sprint4_browser.cjs`）

真实后端，无头 Chromium（Playwright 1.63.0），浏览器时区 Asia/Shanghai，已纳入 CI（`ci-infra.yml`）。
- AFFiNE 地址是不存在的主机 `http://affine.e2e.invalid:3010`：页面只显示链接，浏览器和后端都不应请求它。
- 被关联的 Vikunja 任务直接写进投影。
- GitHub 用模拟服务，与接口级验收的是同一个（第 4 节）。

| 步骤 | 结果 |
|---|---|
| 知识库页配置 AFFiNE 外部地址：`http://169.254.169.254` 在前端被拒绝且没有提交（A12），合法地址保存成功、只提交一次 | 通过 |
| 新增收藏：`javascript:` 链接在前端被拦下，没有发出请求；合法链接保存后已规范化（保留 `mode`，去掉 `refreshKey` 与片段），并关联了任务 | 通过 |
| 任务抽屉的“相关文档”显示关联的收藏 | 通过 |
| 日历新建、编辑日程：时间按 Asia/Shanghai 换算成 UTC 保存，地点保存，版本递增 | 通过 |
| 首页“最近文档”“近期日程”组件显示真实数据 | 通过 |
| 设置页连接 GitHub：细粒度 Token 保存后测试连接，必需检查与逐仓库权限检查都通过；页面始终不出现 Token | 通过 |
| 开发者页：仓库、Issue（Issue 列表里没有 PR）、Actions 运行与故障摘要（`test：Run pytest`） | 通过 |
| A17：模拟 GitHub 主限流后手动同步，开发者页显示“GitHub 限流中，预计 … 恢复”，仓库列表照常显示 | 通过 |
| 顶栏搜索框：收藏、日程、GitHub 仓库按类型分组；点日程结果跳到日历对应日期并显示该日程 | 通过 |
| 390px 宽度下知识库与日历页没有横向滚动 | 通过 |
| viewer：能看收藏、日程与开发者页，没有新增 / 管理 / 删除入口，日程表单只读 | 通过 |
| A10：删除日程与收藏后，页面、搜索接口与顶栏搜索框里都不再出现 | 通过 |
| DoD：全程没有向 AFFiNE 地址发出请求 | 通过 |

页面全程没有脚本错误。知识库与日历部分本机连续 3 次通过；加上 GitHub 与搜索框后，本机又连续 3 次通过。

第一次 CI 失败是脚本的竞态，不是应用的问题：
- 字段在输入时就会显示校验错误。脚本只等这条错误就接着填入合法地址，但被拦下的那次提交还在校验。
- 结果那次提交带着新填的合法地址保存了，弹窗随即关闭，脚本的第二次点击找不到按钮。
- 现在改为等表单级提示：它在提交的校验结束后才出现。脚本同时核对后端只收到一次、而且是合法地址的提交。
- 改后本机又连续通过 3 次。

T-0031 的 CI 里还暴露了 Sprint 1、Sprint 3 浏览器验收脚本各一处潜伏的竞态，都是 claude 写的脚本，已修复：
- Sprint 1：等“其他会话”时用了子串匹配，修改密码表单的说明里也有这四个字。改为精确匹配（PR #47）。
- Sprint 3：Worker 在测试连接后异步注册 Webhook，脚本可能在注册前就在 Vikunja 里改了任务。改为先等 Webhook 注册好（PR #49）。

本机反复运行时还发现一处：脚本结束时会删除模拟 GitHub，但留下的 GitHub 集成仍是启用状态。
- 之后的周期对账连不上这个主机，Connector 按主机计数的熔断器会打开，下一次运行里新建的集成也被拒绝（`UNAVAILABLE`）。
- CI 每次都是全新环境，不受影响。
- 脚本现在在结束前停用自己的 GitHub 集成，改后本机连续 3 次通过。

## 3. 任务清单（docs/TASKS.md Sprint 4）

全部勾选。

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S4-01 AFFiNE Deep Link、收藏和权限可见性，禁止未验证的私有 API | 后端：T-0025 核验 ADR（PR #39）、T-0026 契约（PR #40）、T-0027（PR #41）。前端：T-0030（codex，PR #45） | 深链接按 AFFiNE 0.27.4 源码路由做字符串校验，不调用 AFFiNE 任何接口（DoD）。私有收藏只对创建者可见（A11）。浏览器验收第 1 ~ 3 步 |
| S4-02 本地日历 CRUD / agenda 展示 | T-0026、T-0027；T-0030 | 迁移 0007；时区换算与全天事件；首页“近期日程”。浏览器验收第 4、5 步 |
| S4-03 外部资源统一映射与搜索索引 | T-0029（PR #43） | 迁移 0008，触发器维护索引；tombstone / 删除后立即搜不到（A10） |
| S4-04 GitHub 只读仓库 / Issue / PR / Actions，限流 / 分页处理 | T-0025、T-0026、T-0028（PR #42）；T-0031（codex，PR #48，经 PR #51 合并） | Link 分页、ETag / 304、主 / 次级限流退避（A17）、Token 失效（A13）。浏览器验收第 6 ~ 8 步 |
| S4-05 全局搜索 UI、跨工作空间安全测试 | T-0029；T-0031 | A11（接口级）；顶栏搜索框（浏览器验收第 9 步） |

## 4. 模拟 GitHub 与真实 GitHub 的差异

模拟服务复用后端测试的内存版 GitHub（`services/api/tests/fake_github.py`），在 api 镜像里运行。
它的行为按 T-0025 核验 ADR（`docs/decisions/2026-10-30-github-rest-and-affine-verification.md` 第 2 ~ 5 节）记录的官方文档与实测：
- 版本头不对返回 `400`；Token 不对返回 `401 Bad credentials`。
- 主限流返回 `403` + `x-ratelimit-remaining: 0` + `x-ratelimit-reset`；次级限流返回 `429` + `retry-after`。
- Issue 列表里带 `pull_request` 键的是 PR；分页用 `Link` 头的 `rel="next"`。
- `If-None-Match` 命中返回 `304`，不计额度；仓库改名返回 `301`。
- 缺少单项权限时返回 `403 Resource not accessible by personal access token`。

与真实 GitHub 的差异：

| 方面 | 模拟服务 | 真实 GitHub | 影响 |
|---|---|---|---|
| 接口范围 | 只实现工作台调用的 5 个 GET 接口（仓库、Issue、PR、运行、运行的 jobs）；其他路径返回 404 | 完整 REST API | 工作台只读，不调用其他接口 |
| 字段 | 只有工作台读取的字段 | 完整对象 | 字段映射以 T-0025 记录的官方 OpenAPI 为准，另有真实验证（见下） |
| 查询参数 | 只处理 `per_page` / `page`；不按 `state`、`since` 过滤，总是返回全部 | 按参数过滤 | 工作台对返回的数据按自己的规则投影；增量同步的正确性由 pytest 覆盖 |
| ETag | 按响应体的哈希生成强 ETag | 可能是弱 ETag（`W/"…"`） | 工作台原样回传，两种都适用 |
| 额度 | 单一的 `core` 计数，每个 200 响应减一 | 按资源分桶，与同一用户的其他调用共用 | 低额度提前停下（剩余 50）由 pytest 覆盖 |
| 次级限流 | 只在验收脚本打开时触发，固定 `retry-after: 30` | 按并发、CPU 时间等触发，不可预测，也可能不带 `retry-after` | 没有 `retry-after` 时至少等 60 秒（ADR 第 5 节），由 pytest 覆盖 |
| 传输 | `http://fake-github:8080`（内网，需在 `INTEGRATION_ALLOWED_HOSTS` 里） | `https://api.github.com` | 生产固定为 `api.github.com`，`WORKSPACE_GITHUB_API_URL` 只用于测试 |
| 5xx / 超时 | 不模拟 | 偶尔发生 | 错误分类、超时与重试上限由 pytest 覆盖 |

真实验证：`services/api/tests/test_github_live.py` 需要 `TEST_GITHUB_TOKEN`（细粒度 Token，按 ADR 第 3 节授权）与 `TEST_GITHUB_REPO`。
CI 里没有这两个变量，所以跳过；本机同样没有，**未运行**（见第 6 节）。

## 5. 验收中发现并修复的问题

- **Worker 无法访问 GitHub**（T-0028 的缺陷）：
  - 原因：Worker 只在 `backend` 与 `integrations` 两个内部网络里，没有出网。部署后 GitHub 的周期同步会全部失败（DNS 解析不到 `api.github.com`）。
  - pytest 用 MockTransport，验收用内网的模拟服务，所以之前都没有发现。
  - 修复：新增只给 Worker 用的 `egress` 网络（`compose.yaml`）；出站地址仍由 Connector 的 SSRF 检查限制。
  - 本机实测：去掉 `egress` 后，在 Worker 里解析 `api.github.com` 报 `Temporary failure in name resolution`；加上后能连到 443 端口。
  - 部署文档第 2 节已更新。
- GitHub 的地址之前不能通过 `.env` 改成模拟服务。现在 `compose.yaml` 把 `.env` 里的 `WORKSPACE_GITHUB_API_URL` 作为 `GITHUB_API_URL` 传给 api 与 worker，默认值仍是 `https://api.github.com`。
  - 第一次 CI 失败（PR #44 的 compose job）：`.env` 里直接写 `GITHUB_API_URL`，但 GitHub Actions 会在 shell 里设置同名变量，Compose 插值时 shell 变量优先，api 实际访问了真实的 api.github.com，模拟 Token 被拒（`token_rejected`），模拟服务一个请求都没收到。
  - 本机复现：shell 里设置 `GITHUB_API_URL=https://api.github.com` 后重建 api，容器里的值就是真实地址。改用 `WORKSPACE_GITHUB_API_URL` 后，同样的 shell 环境下容器里是模拟服务的地址。
- **AFFiNE 地址可以用其他写法绕过链路本地检查**（T-0027 的缺陷，A12）：
  - 原因：后端只认标准写法的 IP。`http://2852039166`、`http://0xa9fea9fe`、`http://0251.0376.0251.0376`、`http://169.254.43518` 都被当成主机名放行，但浏览器会把它们解析成 169.254.169.254。
  - 影响很小：工作台从不访问这个地址，只把它当链接给用户自己的浏览器打开。前端用 `new URL()` 先规范化，在页面上已经会拦下。
  - 修复：按 `inet_aton` 的规则解析这些写法，IPv4 映射的 IPv6 也按其中的 IPv4 判断。新增参数化测试，同时确认回环与内网地址仍然允许。
  - 集成地址（服务端会访问）不受影响：Connector 先解析主机名再检查每个地址，这些写法本来就被拒绝，本机实测都是 `422`。

## 6. 实际执行的命令

```bash
docker build …（本机经代理构建 api 镜像）&& docker compose up -d --no-build --wait && docker compose exec -T api alembic upgrade head
python3 tests/e2e/sprint4_acceptance.py      # 31 项全部通过，本机连续 3 次（中间补了次级限流与“新的成功同步”两项检查）
docker build …（本机经代理构建 web 镜像，T-0030 合并后的 main）&& docker compose up -d --no-build --wait
node tests/e2e/sprint4_browser.cjs           # 10 步全部通过（页面无错误），连续 3 次
docker build …（web 镜像，合入 T-0031 后）&& docker compose up -d --no-build --wait
node tests/e2e/sprint4_browser.cjs           # 13 步全部通过（页面无错误），连续 3 次
make vikunja-up && node tests/e2e/sprint3_browser.cjs   # Webhook 竞态修复后连续 2 次通过
docker compose down -v && docker compose up -d --wait && make migrate && node tests/e2e/sprint1_browser.cjs   # 精确匹配修复后通过
```

- Sprint 1 ~ 3 的验收脚本没有在本机重跑（Sprint 3 需要 Vikunja）。它们在同一个 CI 流程里运行，结果见本 PR 的 CI。
- 后端：没有改应用代码，只给 `tests/fake_github.py` 加了地址参数。`cd services/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app migrations tests scripts` 全部通过；`uv run pytest -q`（真实 PostgreSQL + Redis）→ 317 passed, 7 skipped（跳过的是需要真实 Vikunja / GitHub 凭据的测试）。

## 7. 未运行 / 限制

| 项目 | 说明 |
|---|---|
| 真实 GitHub | 需要 `TEST_GITHUB_TOKEN` / `TEST_GITHUB_REPO`，本机与 CI 都没有；T-0025 的核验用的是官方 OpenAPI 与匿名实测 |
| 真实 AFFiNE | 工作台不向 AFFiNE 发请求，只校验链接字符串，没有部署 AFFiNE 实例；路由规则来自 v0.27.4 源码（核验 ADR 第 8 节） |
| 中文分词 | 搜索对中文按字面包含匹配，不分词（部署文档第 11 节） |
| 熔断器按主机计数 | Connector 的熔断器以目标主机为单位：同一主机上的一个集成持续失败，会让其他工作空间指向同一主机的集成也暂时被拒绝。对 `api.github.com` 这类公共服务，故障本来就是全局的，影响有限。是否改成按集成计数，留到 Sprint 6 的安全加固一并评估 |
| DNS 重绑定 | Connector 在发请求前解析并检查地址，但 httpx 连接时会再解析一次，两次之间 DNS 结果可能变化。集成只有工作空间管理员能配置。连接时固定已检查的 IP 留到 Sprint 6 的安全加固 |
