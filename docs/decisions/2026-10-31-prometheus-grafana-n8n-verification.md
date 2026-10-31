# Prometheus 3.15.1 / Grafana 13.2.5 / n8n 2.45.7 核验（T-0033，Sprint 5）

- 日期：2026-10-31
- 作者：claude（T-0033）
- 状态：已核验（未核验的项逐条标注）
- 用途：T-0034 契约与 T-0035（运维与指标）、T-0037（n8n 白名单触发）实现的依据

## 1. 方法与来源

- 镜像 tag 与 digest 来自 Docker Hub 的 tag API（`hub.docker.com/v2/repositories/<repo>/tags/<tag>`），按 digest 拉取后在本机运行实测。
- 文档以各项目在对应 tag 下的源码仓库为准，见第 6 节链接。本环境的网络策略拒绝 prometheus.io、grafana.com、docs.n8n.io，所以没有直接读文档网站。
- 实测在 claude 自己的 Docker 环境里进行（D-0006），测试结束即删除。下文写“实测”的结论都来自这些运行。

## 2. 锁定的版本

| 组件 | 镜像 | digest | 许可证 | 说明 |
|---|---|---|---|---|
| Prometheus | `prom/prometheus:v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` | Apache-2.0 | 2026-10-21 发布；`/api/v1/status/buildinfo` 实测为 3.15.1，revision `bae3ece` |
| Grafana | `grafana/grafana:13.2.5` | `sha256:9f7aa1e1e3246aade5792d35a738049f7f32f89cd51d1956cf426a4e86268a84` | AGPL-3.0 | 2026-10-27 发布；`/api/health` 实测为 13.2.5，commit `3ab9ef0` |
| n8n | `n8nio/n8n:2.45.7` | `sha256:9a8d8dcea75667496695d7810d7e1fe018a65efb36cc499d347f3931b3957b5d` | Sustainable Use License（`.ee` 文件另有企业许可） | 2026-10-30；是 `stable` 与 `latest` 指向的版本（`next` 是 2.46.3，不用） |
| Python 指标库 | `prometheus-client==0.26.0` | — | Apache-2.0 AND BSD-2-Clause | 工作台 api 暴露自身指标时使用（T-0035 加入依赖并锁定） |

- 生产不用浮动 tag（项目规则），Compose 里写 tag + digest。
- 许可证：
  - Grafana（AGPL-3.0）作为独立服务原样运行，工作台只生成外链，不修改也不链接它的代码。
  - n8n 的 Sustainable Use License 允许自托管用于内部业务。规范 7.5 要求：对外商业托管、或让其他客户配置工作流之前重新核验许可证。

## 3. Prometheus

### 3.1 部署与安全边界（实测）
- 管理接口默认关闭：
  - `POST /api/v1/admin/tsdb/snapshot` 返回 `500 {"errorType":"unavailable","error":"admin APIs disabled"}`。
  - 生命周期接口 `/-/reload`、`/-/quit` 返回 `403 Lifecycle API is not enabled`。
  - 部署时**不加** `--web.enable-admin-api`、`--web.enable-lifecycle`、`--web.enable-remote-write-receiver`（`/api/v1/status/flags` 实测三项都是 `false`）。
- `GET /api/v1/status/config` 在默认配置下可以读到完整配置。所以 Prometheus **只能放在内部网络**：不发布端口，不经 Caddy 转发；浏览器永远只能访问工作台的 BFF（Sprint 5 DoD）。
- 保留期：`--storage.tsdb.retention.time=7d`，实测 flags 显示为 `1w`（S5-05：详细数据保留 7 天）。

### 3.2 HTTP API（实测 + `docs/querying/api.md`）
- 即时查询：`GET /api/v1/query?query=<PromQL>[&time=][&timeout=]`。
- 区间查询：`GET /api/v1/query_range?query=&start=&end=&step=[&timeout=]`。
- 成功：`{"status":"success","data":{"resultType":"vector|matrix|scalar|string","result":[...]}}`。
  - 样本值是字符串，例如 `[1793409281.516,"1"]`；matrix 的点在 `values` 数组里。
- 失败：`{"status":"error","errorType":"<string>","error":"<string>"}`。
  - 表达式错误返回 `400 bad_data`（实测：`up{` → `parse error`）。
  - 文档另列出：无法执行返回 `422`，超时或中止返回 `503`。
- 由此确定 Connector 的错误归类：
  - `400` / `422` 是我们自己的查询有误，记为内部错误，不重试。
  - `503`、超时、连接失败记为 `UNAVAILABLE`，可以重试，页面显示 stale。

### 3.3 预设指标视图（规范 7.4）
- 工作台**只发送固定的查询**：按视图 ID 选择预设的 PromQL，时间范围取预设档位。不接受浏览器传入的任意 PromQL。
- V1 视图与查询草案如下。指标名在 T-0034 契约中定稿，由 T-0035 实现：

| 视图 | 查询（草案） | 来源 |
|---|---|---|
| 请求次数 | `sum(rate(workspace_http_requests_total[5m]))` | 工作台 api 自身的指标（prometheus-client） |
| 延迟 P50 / P95 / P99 | `histogram_quantile(0.95, sum by (le) (rate(workspace_http_request_duration_seconds_bucket[5m])))`（0.5 / 0.99 同理） | 同上，直方图 |
| 5xx 率 | `sum(rate(workspace_http_requests_total{status_class="5xx"}[5m])) / sum(rate(workspace_http_requests_total[5m]))` | 同上 |
| 服务 up / down | `up{job=~"workspace-.*"}` | Prometheus 抓取结果 |
| CPU / 内存 | 只在部署了 exporter 时提供 | **未核验**：V1 不默认带 exporter，T-0035 视情况决定 |

- 指标标签只用路由模板（例如 `/api/v1/tasks/{task_id}`）和状态码类别，不放原始路径、查询串、用户输入或 Token（S5-05“无敏感日志”）。
- `/metrics` 挂在 api 上。Caddy 只把 `/api/*` 与 `/healthz` 转给 api（`/readyz` 只接受内网），所以 `/metrics` 从外部访问不到，只在 Compose 内部网络里供 Prometheus 抓取。T-0035 要用测试确认 Caddy 不转发它。

## 4. Grafana

- 实测默认值：
  - 匿名访问关闭：`/api/search` 返回 401；未登录访问 `/d/<uid>/<slug>` 302 跳到 `/login?redirectTo=…`。
  - `allow_embedding = false`，`cookie_samesite = lax`。
- 数据源预置：`/etc/grafana/provisioning/datasources/*.yml`（`apiVersion: 1`，`type: prometheus`，`access: proxy`，`editable: false`）。实测加载后数据源为只读，`/api/datasources/uid/<uid>/health` 返回 `Successfully queried the Prometheus API`。
- 仪表盘地址：创建仪表盘的响应里 `url` 为 `/d/<uid>/<slug>`（实测）。时间范围参数 `?from=now-6h&to=now` 按官方文档，未在界面上逐项核验。
- 工作台对 Grafana 的处理（规范 7.4：复杂查询与告警留在 Grafana，本系统只展示摘要和外链）：
  - 只保存浏览器访问 Grafana 的地址和可选的仪表盘 UID，拼出外链。
  - 不调用 Grafana API，不保存 Grafana 的账号或 Token，也不内嵌 iframe（Grafana 默认禁止嵌入，我们也不打开）。
  - 管理员密码用 `GF_SECURITY_ADMIN_PASSWORD` 设置；匿名访问保持关闭。
  - `GF_ANALYTICS_REPORTING_ENABLED=false`、`GF_ANALYTICS_CHECK_FOR_UPDATES=false`，不向外报告。

## 5. n8n

### 5.1 运行（实测）
- 容器默认监听 `::`。在没有 IPv6 的网络里会直接退出：`n8n's address '::' is not available`。Compose 必须设 `N8N_LISTEN_ADDRESS=0.0.0.0`。
- 健康检查：
  - `/healthz` 在启动初期就返回 `{"status":"ok"}`。
  - `/healthz/readiness` 在数据库迁移完成前返回 `503`。
  - 所以 Compose 的 healthcheck 与工作台的“服务可达”检查都用 `/healthz/readiness`。
- 必要配置：
  - `N8N_ENCRYPTION_KEY`：固定，丢失后已存的凭据无法解密。
  - `N8N_DIAGNOSTICS_ENABLED=false`、`N8N_PERSONALIZATION_ENABLED=false`。
  - 经 HTTPS 访问编辑器时保持 `N8N_SECURE_COOKIE` 默认值；本机 HTTP 实测时设为 `false`。

### 5.2 受控触发：Webhook 节点 + Header Auth（实测）
- 白名单里的工作流以 Webhook 节点（`n8n-nodes-base.webhook`，typeVersion 2）作为入口：
  - `httpMethod: POST`，`path: <固定路径>`，`authentication: headerAuth`。
  - 凭据类型 `httpHeaderAuth`，例如请求头 `X-Workspace-Trigger: <共享密钥>`。
- 激活后的生产地址是 `POST /webhook/<path>`（实测）：
  - 不带或带错密钥：`403 Authorization data is wrong!`。
  - 密钥正确（`responseMode: onReceived`）：`200 {"message":"Workflow was started"}`，工作流异步执行。
  - `/webhook-test/<path>` 只在编辑器的测试模式下生效，否则 `404`。工作台不用它。
- 工作台只调用白名单里登记的路径，带上共享密钥。密钥加密保存在工作台（同 Vikunja / GitHub 凭据），不下发到浏览器（规范 7.5）。
- **未核验**：`onReceived` 的响应里没有执行 ID。要把一次触发和它的执行记录对应起来，T-0037 需要选一种办法并实测：
  - 让白名单工作流用 “Respond to Webhook” 节点返回 `{{$execution.id}}`；
  - 或者按触发时间窗口和 workflowId 去执行记录里匹配。
  - 在核验之前，契约里的“执行 ID”按可空处理。

### 5.3 读取工作流与执行记录：Public API（实测）
- 基础路径 `/api/v1`。OpenAPI 由实例在 `/api/v1/openapi.yml` 提供（实测 200，`info.version: 1.1.1`），共 107 个路径。
- 认证：请求头 `X-N8N-API-KEY`（`securitySchemes.ApiKeyAuth`）；不带时返回 `401 {"message":"Unauthorized"}`。
- API Key 可以限定 scope。实测创建了只读 Key（`workflow:list`、`workflow:read`、`execution:list`、`execution:read`）：
  - 能列出工作流与执行。
  - 激活工作流、删除执行都返回 `403 Forbidden`。
  - 工作台只要求这四个 scope，不接受管理级 Key（规范 7.5：不把管理 Token 下发到浏览器；最小权限）。
- 执行列表：`GET /executions?workflowId=&status=&limit=&cursor=`（另有 `startedAfter`、`startedBefore`、`projectId`、`includeData`、`redactExecutionData`）。
  - 用 `nextCursor` 翻页。
  - `status` 取值：`canceled`、`crashed`、`error`、`new`、`running`、`success`、`unknown`、`waiting`。
  - `mode` 取值：`cli`、`error`、`integrated`、`internal`、`manual`、`retry`、`trigger`、`webhook`、`evaluation`。
- 执行详情 `GET /executions/{id}` 不带 `includeData` 时只有状态与时间字段（`id`、`status`、`mode`、`finished`、`startedAt`、`stoppedAt`、`workflowId`、`workflowVersionId` 等，实测），**不含节点数据**。带 `includeData=true` 才返回完整数据。
- 工作台的做法（规范 7.5：持久化错误摘要，不保存第三方完整敏感 payload）：
  - 默认不带 `includeData`。
  - **未核验**：失败时读取错误摘要的方式。候选是 `includeData=true&redactExecutionData=true`，只取 `resultData.error.message`、其余丢弃。T-0037 实测后再定，在那之前只记状态，不记错误正文。
- 工作流列表：`GET /workflows?limit=&cursor=`，返回 `id`、`name`、`active` 等，用于白名单登记时核对工作流存在且已激活。

## 6. 结论与后续任务

- **T-0034（契约）**：
  - 预设指标视图用 ID 选择，不透传 PromQL；Grafana 只给外链。
  - n8n 作为新的集成类型，凭据为只读 API Key 加 Webhook 共享密钥，白名单登记路径与参数 schema。
  - 执行 ID 可空；执行状态取第 5.3 节的枚举。
- **T-0035（运维与指标）**：
  - 加 `prometheus-client==0.26.0`，`/metrics` 只在内部网络。
  - 可选 `infra/monitoring/`：Prometheus（不开管理与生命周期接口、保留 7 天、不发布端口）+ Grafana（匿名关闭、只读数据源）。
  - Prometheus Connector 的错误归类按第 3.2 节。
- **T-0037（n8n）**：
  - 可选 `infra/n8n/`：`N8N_LISTEN_ADDRESS=0.0.0.0`，readiness 健康检查，固定加密密钥，加入 integrations 网络，编辑器不经工作台的 Caddy 暴露。
  - 实测第 5.2、5.3 节标为未核验的两项。
- **T-0040（验收）**：CI 是否运行真实 n8n（镜像约 1.8 GB）还是模拟服务，届时按耗时决定，并写明与真实服务的差异。
- **未核验汇总**：
  - CPU / 内存 exporter；
  - Grafana 时间范围参数在界面上的效果；
  - 触发与执行记录的对应方式；
  - 错误摘要的读取方式。

## 7. 来源

- Prometheus v3.15.1：
  - <https://raw.githubusercontent.com/prometheus/prometheus/v3.15.1/docs/querying/api.md>（响应格式、错误码、TSDB Admin APIs 需要 `--web.enable-admin-api`）
  - <https://raw.githubusercontent.com/prometheus/prometheus/v3.15.1/LICENSE>
- Grafana v13.2.5：<https://raw.githubusercontent.com/grafana/grafana/v13.2.5/LICENSE>
- n8n 2.45.7：
  - <https://raw.githubusercontent.com/n8n-io/n8n/n8n%402.45.7/LICENSE.md>
  - 运行实例提供的 `/api/v1/openapi.yml`（n8n Public API 1.1.1）
- Docker Hub tag API：`prom/prometheus`、`grafana/grafana`、`n8nio/n8n`（tag 与 digest、发布日期）
- PyPI：<https://pypi.org/pypi/prometheus-client/json>（0.26.0）
