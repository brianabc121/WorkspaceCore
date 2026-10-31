# Sprint 5 验收报告

- 验收人：claude（协作任务 T-0040，按 D-0003 负责每个 Sprint 的联调与验收；codex 暂停后按 D-0007 独立完成所有任务）
- 日期：2026-10-31
- 结论：**Sprint 5 的 DoD 与 A16 全部通过**。接口级（30 项）与浏览器（15 步）验收都已纳入 CI。“未运行 / 限制”逐条见第 7 节。
- 是否进入 Sprint 6：人类已在 2026-10-30 预先批准“验收通过就自动进入”（记录在 T-0032 的进度日志），本次验收通过，按此进入 Sprint 6。
  - 2026-10-31 人类在对话中暂停了 AI 厂商选型（记录在 T-0040 的进度日志）。所以 Sprint 6 先做不依赖它的 S6-04 ~ S6-06；S6-01 ~ S6-03（AI）等人类恢复选型后再开始。
  - 同一天人类决定 codex 暂停，所有任务由 claude 独立完成（D-0007）。

全栈环境：Compose 6 个核心服务，外加：
- `prometheus`（`--profile monitoring`，只启动 prometheus；Grafana 不在 CI 启动，见第 7 节）；
- 一个模拟 n8n 服务（`tests/e2e/fake_n8n_server.py`，见第 4 节），加入 integrations 网络，主机名 `fake-n8n`。

CI 的 `.env` 有两处改动：`PROMETHEUS_URL=http://prometheus:9090`，`INTEGRATION_ALLOWED_HOSTS=vikunja,fake-github,fake-n8n`。
CI 里没有真实的 n8n、Grafana 凭据。两个验收脚本都不要求空库：每次运行都新建工作台用户（`tests/e2e/seed.py`）。

## 1. Sprint 5 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 不向浏览器暴露 Prometheus 管理端 / Secret | 通过 | 接口级：Prometheus 没有发布端口（只在内部网络）；经 Caddy 访问 `/metrics` 得不到 api 的指标；指标接口只接受预设视图 ID，传 PromQL 返回 `422`；服务状态与指标响应里没有内部地址和 PromQL；n8n 的 API Key 与共享密钥不出现在任何响应里。<br>浏览器：全程的请求都指向工作台自己的地址，没有请求 Prometheus 接口（本机一次运行共 280 个请求）；接口响应（190 个）与页面里没有内部地址、`.env` 里的密钥、n8n 的 API Key 与共享密钥 |
| 失败明确显示 stale | 通过 | 停掉 Prometheus 后：指标接口返回之前的结果并标为 `stale`；运维页与首页“API 延迟”组件显示“数据可能过期（截至 …）”和缓存的视图，Prometheus 显示“不可用”，其他服务照常；恢复后不再提示过期。<br>模拟 n8n 不可用后：白名单照常显示，连接标为 `stale` |
| 触发自动化有审计记录 | 通过 | 受理与被策略拒绝的触发都有 `automation.triggered` 审计，只记参数名；审计、执行记录、通知里没有参数值与密钥 |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A16 断开 SSE 后重连 | 页面能重新拉取最新投影 | 通过。<br>• 接口级：经 Caddy 的 SSE 没有被压缩，写入后很快收到失效事件（本机约 50 ms）；断开期间的变化，带 `Last-Event-ID` 重连后补发；无法续传时发 `reset`；重新拉取得到最新数据。<br>• 浏览器：Caddy 停掉期间写入的通知，在浏览器自动重连后出现在顶栏未读数里；api 重启（期间 Caddy 返回 502）后 SSE 恢复，新通知照常推送；页面全程没有重新加载 |

### 2.1 接口级（`tests/e2e/sprint5_acceptance.py`，30 项）

全部通过：
- **S5-01**：6 个预设视图（请求速率、延迟 P50 / P95 / P99、5xx 占比、服务在线）来自真实 Prometheus。
- **S5-02**：核心服务（api、PostgreSQL、Redis）与 Prometheus 正常；Worker 心跳正常。
- **S5-03**：通知去重——同一次执行失败，对账后再过一轮，仍只通知一次。
- **S5-04**：
  - n8n 连接测试通过；
  - 登记白名单工作流；n8n 里没有的工作流不能登记（`422`）；
  - 未声明的参数返回 `422`；
  - 同一个 `Idempotency-Key` 只触发一次，n8n 只收到声明过的参数；
  - 对账后执行记录为 `error`，只有遮蔽后的错误摘要；
  - 停用的白名单拒绝触发（`409`，`reason: disabled`）。
- **S5-05**：Prometheus 保留 7 天（`--storage.tsdb.retention.time=7d`），管理与生命周期接口关闭；访问日志不记录查询串。

### 2.2 浏览器（`tests/e2e/sprint5_browser.cjs`，15 步）

真实后端，无头 Chromium（Playwright 1.63.0），已纳入 CI（`ci-infra.yml`）。n8n 用模拟服务，与接口级验收的是同一个。

| 步骤 | 结果 |
|---|---|
| 运维页服务状态：API、PostgreSQL、Redis、Worker、Prometheus 正常，显示最近检查时间；未部署的 Grafana 显示“未部署 / 未配置” | 通过 |
| 预设指标：6 个视图带“数据截至”；切换时间范围与视图时只提交预设 ID，不提交 PromQL；没有配置 Grafana 看板地址时不显示 Grafana 链接 | 通过 |
| 首页“服务状态”组件显示各服务；“API 延迟”组件显示 `P95 · 1h` 的视图 | 通过 |
| 通知：写入后不刷新页面，顶栏未读数经 SSE 加一；通知中心按纯文本显示正文（`<b>` 原样显示）、链接是应用内地址；首页通知组件标为已读后顶栏同步减一 | 通过 |
| A16：Caddy 断开期间写入的通知在重连后出现；api 重启（期间 502）后 SSE 恢复，新通知照常推送，页面没有重新加载 | 通过 |
| 通知中心“全部标为已读”后顶栏未读数为 0 | 通过 |
| 自动化页：配置 n8n 连接后只显示“已设置”，测试连接通过；登记白名单工作流（含敏感参数与数字参数），状态为已激活；页面不出现 API Key 与共享密钥 | 通过 |
| 触发：必填参数在前端被拦下；确认页隐藏敏感值；确认前不提交；确认后只提交一次，带 UUID 形式的 `Idempotency-Key`，n8n 只收到声明过的参数 | 通过 |
| 运行记录：Worker 对账后，不刷新页面即显示“失败”与遮蔽后的错误摘要（“工作流在节点「发送邮件」失败（WrappedExecutionError）”） | 通过 |
| 结果未知：n8n 响应超时后页面显示“结果未知，正在确认”，不自动重发（n8n 只收到一次）；对账按 traceparent 对上后运行记录变为“成功” | 通过 |
| DoD：停掉 Prometheus 后运维页与首页“API 延迟”组件显示“数据可能过期（截至 …）”与缓存的视图，Prometheus 显示“不可用”，其他服务照常 | 通过 |
| Prometheus 恢复后服务状态与指标恢复正常，不再提示过期 | 通过 |
| 390px 宽度下运维页、自动化页与首页没有横向滚动 | 通过 |
| viewer：能看运维页与首页运维组件；通知中心只有自己的；自动化页能看白名单与运行记录，没有触发、连接设置、编辑与移出入口 | 通过 |
| DoD：浏览器请求只指向工作台自己的地址，响应与页面里没有内部地址与密钥 | 通过 |

页面全程没有脚本错误。断开期间浏览器记录的连接失败（`ERR_CONNECTION_REFUSED`、`ERR_INCOMPLETE_CHUNKED_ENCODING`、`502`）是预期的，脚本只在断开窗口内忽略这几类。

## 3. 任务清单（docs/TASKS.md Sprint 5）

全部勾选。

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S5-01 Prometheus/Grafana 可选 Compose、API 预设指标视图 | 核验：T-0033（PR #53）；契约：T-0034（PR #54）；后端：T-0035（PR #55）；前端：T-0038（codex，PR #58） | `--profile monitoring`，镜像按 tag 加 digest 锁定；6 个预设视图（接口级 S5-01，浏览器第 2、3 步） |
| S5-02 Service status、健康告警、降级状态 | T-0035、T-0036（PR #56）；T-0038 | 服务状态与 Worker 心跳；连续两次检查失败才告警，恢复时再通知一次（T-0036 实测）；Prometheus 不可用时显示 stale（DoD） |
| S5-03 通知中心、SSE 重连与缓存失效 | T-0036；T-0039（codex 实现，PR #61；claude 按 D-0007 接手修复 SSE 重连） | 通知去重（接口级）；SSE 推送与 A16（接口级、浏览器第 4 ~ 6 步） |
| S5-04 n8n 白名单触发及运行日志，密钥隔离 | T-0033、T-0037（PR #57）；T-0039 | 白名单、参数校验、幂等、遮蔽后的错误摘要、结果未知（接口级 S5-04，浏览器第 7 ~ 10 步）；密钥只写不读（DoD） |
| S5-05 监控保留 7 天策略及无敏感日志检查 | T-0035 | Prometheus 保留 7 天、管理接口关闭；访问日志不记录查询串（接口级 S5-05）；n8n 只读遮蔽后的执行数据（T-0037 ADR 第 3 节） |

## 4. 模拟 n8n 与真实 n8n 的差异

模拟服务复用后端测试的内存版 n8n（`services/api/tests/fake_n8n.py`），在 api 镜像里运行。
它的行为按 T-0037 的实测 ADR（`docs/decisions/2026-10-31-n8n-trigger-and-execution-verification.md`）编写：
- Webhook 用 Header Auth（`X-Workspace-Trigger`）：路径不存在或未激活返回 404，密钥不对返回 403，受理返回 `200 {"message":"Workflow was started"}`，不含执行 ID。
- 触发请求的 `traceparent` 记在执行**详情**的 `tracingContext` 里，执行列表里没有。
- 执行列表按 `workflowId` / `startedAfter` 筛选；详情在 `includeData=true&redactExecutionData=true` 时只给节点名与错误类型。
- Public API 用 `X-N8N-API-KEY`，不对返回 401。

与真实 n8n 2.45.7 的差异：

| 方面 | 模拟服务 | 真实 n8n | 影响 |
|---|---|---|---|
| 工作流执行 | 不执行，按控制接口设定的状态直接生成执行记录 | 执行工作流节点 | 工作台只关心执行状态与错误摘要；真实执行已在 T-0037 的 ADR 第 5 节实测 |
| 读超时 | 控制接口打开后，受理请求但 15 秒后才响应 | 网络或实例负载导致 | 工作台按“结果未知”处理，与真实场景一致 |
| 激活延迟 | 登记后立即可触发 | 刚激活后约 1 秒内 Webhook 返回 404 | 工作台按 `webhook_not_found` 处理、不自动重试（ADR 第 2 节），由 pytest 覆盖 |
| 遮蔽后的数据 | 只有 `lastNodeExecuted`、`redactedError.type` | 还有 `nodeExecutionStack` 等（工作台不读） | 工作台只取这两项 |
| 版本号 | 不提供 | 设置 owner 后也不对外提供 | 测试连接的版本检查都记为 `not_reported`（ADR 第 4 节） |
| 分页 | 一页返回全部 | `nextCursor` 翻页 | 对账只按 `startedAfter` 看最近的执行；翻页由 pytest 覆盖 |

## 5. 验收中发现并修复的问题

- **api 重启后 SSE 不再重连**（T-0039 的缺陷，PR #61 审查时发现）：
  - 现象：api 重启期间，Caddy 对浏览器的重连请求返回 502。EventSource 收到非 200 就永久关闭，之后页面不再实时刷新，直到用户刷新页面。
  - 修复：EventSource 被关闭后按退避重新建立（1 秒起翻倍，最长 30 秒），成功连上后清零；切换工作空间、登出时取消。codex 没有额度继续，claude 按 D-0007 接手修复。
  - 测试：Vitest 新增“关闭后按退避重建”“切换或登出后取消重建”两条用例（前者在修复前失败）；浏览器验收第 5 步覆盖 api 重启的场景。
- **验收脚本留下启用的 n8n 连接**：
  - 接口级验收结束时会删除模拟 n8n，但它的 n8n 连接仍是启用状态。之后 Worker 每分钟对账都连不上这个主机，按主机计数的熔断器可能打开，影响随后的浏览器验收（同一个主机 `fake-n8n`）。
  - 两个脚本现在都在结束前停用自己的 n8n 连接（Sprint 4 的 GitHub 集成也是这样处理的）。

## 6. 实际执行的命令

```bash
cd apps/web && npm ci && npm run lint && npm run typecheck && npm run test && npm run build && npm run check:api   # 25 个文件 262 个测试通过
docker build …（本机经代理构建 web 镜像：PR #58、PR #61 与修复后的前端）&& docker compose up -d --no-build --wait
docker compose --profile monitoring up -d --wait prometheus
python3 tests/e2e/sprint5_acceptance.py      # 30 项全部通过
node tests/e2e/sprint5_browser.cjs           # 15 步全部通过（页面无错误）
GRAFANA_URL= GRAFANA_DASHBOARD_URL= docker compose up -d --no-build --wait api worker   # 模拟 CI 不部署 Grafana
node tests/e2e/sprint5_browser.cjs           # 同样通过
```

- 本机是 claude 自己的 Docker（D-0006）。Sprint 1 ~ 4 的验收没有在本机重跑，它们在同一个 CI 流程里运行，结果见本 PR 的 CI。

## 7. 未运行 / 限制

| 项目 | 说明 |
|---|---|
| Grafana 不在 CI 启动 | 镜像约 2 GB。它的安全默认值（匿名关闭、禁止嵌入、只读数据源、没有管理员密码时拒绝启动）已在 T-0035 实测；本机浏览器验收在配置了 Grafana 看板地址时，验证了“在 Grafana 中查看”外链 |
| 真实 n8n 不在 CI 启动 | 镜像约 1.8 GB，也没有凭据；全流程已在 T-0037 的 ADR 第 5 节实测，CI 用第 4 节的模拟服务 |
| 告警的时间相关部分 | “连续两次失败才告警”“恢复时再通知一次”需要等两分钟以上，由 T-0036 的 pytest 与当时的本机实测覆盖，不在 CI 的验收脚本里 |
| SSE 每 10 分钟主动断开 | 浏览器会带 `Last-Event-ID` 自动重连。验收脚本运行不到 10 分钟，没有覆盖这一次断开；同一机制由“Caddy 断开”那一步覆盖 |
| 前端初始包超过 500 kB | 构建只给出警告；按页面拆包留到后续 |
| 熔断器按主机计数、DNS 重绑定 | 见 Sprint 4 验收报告第 7 节，留到 Sprint 6 的安全加固 |
