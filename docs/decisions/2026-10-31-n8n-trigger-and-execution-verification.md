# n8n 2.45.7：触发与执行记录的对应、错误摘要（T-0037 实测）

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0037）
- 补充：`docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`（下称“核验 ADR”）第 5.2、5.3 节标为“未核验”的两项。
- 契约：`docs/decisions/2026-10-31-sprint-5-operations-automations-contract.md` 第 6 节。

## 1. 方法

- 镜像：`n8nio/n8n:2.45.7@sha256:9a8d8dcea75667496695d7810d7e1fe018a65efb36cc499d347f3931b3957b5d`。
- 实例：claude 自己的 Docker（D-0006）里的临时实例，用完即删。
- 准备步骤：
  - 设置 owner；
  - 创建只读 API Key（`workflow:list`、`workflow:read`、`execution:list`、`execution:read`）；
  - 创建 Header Auth 凭据（`X-Workspace-Trigger`）；
  - 创建并激活工作流：Webhook（`responseMode: onReceived`）→ Code 节点，参数里 `fail: true` 时抛错。
- 接口描述：以实例自带的 `/api/v1/openapi.yml`（`info.version: 1.1.1`）为准，与 T-0033 保存的副本一致。
- 全栈实测：`make n8n-up` 启动的实例，加上工作台的 api / worker，经 Caddy 访问。结果见第 5 节。

## 2. 触发与执行记录的对应（核验 ADR 第 5.2 节）

- `onReceived` 的响应是 `200 {"message":"Workflow was started"}`，**不含执行 ID**（与 T-0033 一致）。
- **触发请求带的 W3C `traceparent` 请求头**会原样出现在执行**详情** `GET /api/v1/executions/{id}` 的 `tracingContext.traceparent` 里（实测两次，值完全一致）。
- 执行**列表** `GET /api/v1/executions` 的条目里**没有** `tracingContext`。
  - 实测返回的字段：`id`、`status`、`mode`、`finished`、`startedAt`、`stoppedAt`、`workflowId`、`retryOf`、`retrySuccessId`、`waitTill`。
- 列表的筛选参数（实测）：
  - `workflowId`；
  - `startedAfter`：设为未来时间时返回空；
  - `status`：`status=error` 只返回失败的那条；
  - `limit`；
  - `nextCursor` 翻页。
- 执行不存在时返回 `404 {"message":"Not Found"}`。
- **结论（工作台的做法）**：
  - 每次触发生成执行记录 ID，把它的 32 位十六进制作为 trace-id：`traceparent: 00-<执行记录 ID 的 hex>-<随机 16 位>-01`；
  - 对账时按 `workflowId` 与 `startedAfter`（触发时间往前 2 分钟，防时钟偏差）列出候选，再逐个读详情，按 trace-id 精确对应；
  - 不按时间猜，同一工作流并发触发也不会对错。
- 刚激活后的约 1 秒内触发返回 `404 The requested webhook "POST <path>" is not registered`，随后正常。
  - 工作台按“路径不存在或未激活”处理（`409 AUTOMATION_UNAVAILABLE`，`reason: webhook_not_found`），不自动重试。
  - 下一轮对账读到工作流是激活的，就恢复可触发。

## 3. 错误摘要（核验 ADR 第 5.3 节）

- `GET /api/v1/executions/{id}?includeData=true&redactExecutionData=true`（实测，失败的执行）：
  - `data.redactionInfo = {"isRedacted": true, "reason": "user_requested", "canReveal": true}`；
  - `data.resultData.error` 为 `null`（错误信息也被遮蔽）；
  - 保留的字段：
    - `data.resultData.lastNodeExecuted`（失败的节点名，例如 `Code`）；
    - `data.resultData.redactedError.type`（例如 `WrappedExecutionError`）；
    - 各节点的 `executionStatus`。
  - 返回内容里没有触发参数，也没有错误正文：搜索参数里的邮箱和错误消息，都找不到。
- 不带 `redactExecutionData` 时，按工作流的遮蔽策略处理；实测的默认策略是 `production: false`，也就是**不遮蔽**，会返回完整数据。
  - `redactExecutionData=false` 需要 `execution:reveal` scope，工作台不申请。
- 遮蔽后的数据里仍有 `executionData.nodeExecutionStack[].node.parameters`（工作流定义，例如 Code 节点的代码）和 `resumeToken`。
- **结论（工作台的做法）**：
  - 只在执行失败时读一次遮蔽后的详情，只取 `lastNodeExecuted` 与 `redactedError.type`，生成摘要“工作流在节点「X」失败（Y）”，最多 500 字。
  - 其余内容不保存、不写日志。
  - 错误正文需要时到 n8n 编辑器里看；契约中的 `public_url` 就是给浏览器打开编辑器用的。

## 4. 实例版本

- T-0033 时，在设置 owner **之前**调用 `GET /rest/settings`，能读到 `versionCli`。
- 设置 owner 之后，未登录的 `/rest/settings` 只返回精简的公开设置，没有版本号。
  - 实测返回的键：`authCookie`、`communityNodesEnabled`、`defaultLocale`、`enterprise`、`previewMode`、`settingsMode`、`sso`、`userManagement`。
- Public API 没有版本接口：`openapi.yml` 的 `info.version: 1.1.1` 是 API 的版本，不是实例的版本。
- 响应头里也没有版本号。
- **结论**：
  - 测试连接的 `version` 检查记为 `ok: true`、`code: not_reported`，并给出警告，请管理员确认部署的是 2.45.7；
  - `infra/n8n/compose.yaml` 已按 tag 加 digest 锁定；
  - 不使用 n8n 的内部 `/rest` 接口。

## 5. 全栈实测（`make n8n-up` + 工作台）

| 步骤 | 结果 |
|---|---|
| `PUT /automations/connection`（`base_url: http://n8n:5678`） | 200；响应里没有 API Key 和共享密钥 |
| `POST /automations/connection/test` | `ok: true`，`status: active`；reachable / version / token 都通过 |
| 登记白名单 | 201，`workflow_active: true` |
| 登记不存在的工作流 | 422，`reason: workflow_not_found` |
| 触发（成功的参数） | 202 `queued` |
| 触发（`fail: true`），同一个 Idempotency-Key 再发一次 | 202 `queued`；重放返回同一条执行记录，n8n 只执行了一次 |
| Worker 对账（每分钟第 15 秒） | 两条执行都按 traceparent 对上 n8n 的执行 ID |
| 结果 | 成功的为 `success`；失败的为 `error`，摘要是“工作流在节点「发送邮件」失败（WrappedExecutionError）” |
| 通知 | 触发者收到 `automation_succeeded` 与 `automation_failed` |
| 审计 | 两条 `automation.triggered`，只记参数名 `['fail', 'recipient']` / `['recipient']` |
| 泄露检查 | 执行记录、通知、审计里都没有 API Key、共享密钥和参数值 |
| 运维页与首页 | 运维页 `n8n` 为 `ok`；首页 `automations` 模块为 `ok` |

## 6. 后果

- T-0037 按第 2、3 节实现：
  - `app/integrations/n8n.py`、`app/domain/automations.py`；
  - 内存版 n8n `tests/fake_n8n.py` 按本 ADR 的行为编写。
- 核验 ADR 第 6 节的“未核验汇总”里，“触发与执行记录的对应方式”“错误摘要的读取方式”两项至此核验完毕。
- 升级 n8n 时要重测：
  - `tracingContext.traceparent` 是否仍随执行保存；
  - 遮蔽后是否仍保留 `lastNodeExecuted` 与 `redactedError.type`。
