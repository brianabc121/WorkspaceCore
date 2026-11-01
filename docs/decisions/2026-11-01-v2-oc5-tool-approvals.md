# V2 OC-5：MCP 工具调用记录与审批的实现决定

- 状态：accepted
- 日期：2026-11-01
- 作者：claude（协作任务 T-0057，OC-5 第 1 批）
- 依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.6 节；`docs/decisions/2026-10-31-v2-oc1-contract.md` 第 2、6 节
- 范围：第 1 批只做记录表、审批流程和执行器；`POST /mcp`、读工具、审批卡片在后续批次（见第 8 节）

契约已经定了接口形态，这里记录实现时做出的、契约里没有写死的决定。

## 1. 记录表 `ai_tool_executions`（迁移 0017）

- 每次工具调用一行：读工具直接是 `succeeded` / `failed`；权限不够是 `denied`；写工具和高危工具先是
  `pending_approval`，之后是 `rejected` / `expired`，或者 `executing` → `succeeded` / `failed` / `unknown`。
  状态和风险等级都有 CHECK 约束。
- 实例被删除时记录保留（`instance_id` 置空，另存 `instance_name`）；工作空间删除时一起删除。
- 同一实例、同一工具、同样参数同时只能有一条待审批：部分唯一索引
  `(instance_id, tool_name, arguments_hash) WHERE status = 'pending_approval'`。并发的同样请求只会建一条，
  后到的拿到已有那一条（`deduplicated: true`）。
- 只新增表，`downgrade` 删表即可回退；没有改动已有数据。

## 2. 执行身份

- 批准后以实例的登记人（`acting_user_id`）的身份执行，角色取“登记人在工作空间里的角色”和实例 `max_role`
  中较低的一个（契约第 2 节）。审批人只决定“做不做”，不会把自己的权限借给 AI。
- 执行前重新检查：实例已停用或已删除、总开关关闭、登记人已不是成员或角色不够，都以 `failed` 结束
  （`AGENT_DISABLED` / `FORBIDDEN_ROLE` / `FORBIDDEN_SCOPE`），不执行。
- 执行器直接调用已有的领域服务（任务、日程、书签、通知、自动化），所以工作空间范围、角色校验、审计、
  Vikunja 的超时与重试、结果未知的处理都沿用 V1 的实现，没有另写一套。
- 幂等键是记录 ID：任务写操作和触发自动化用它作 `Idempotency-Key`，同一条审批不会执行两次。
  批准时先把状态改成 `executing` 并提交，再执行；进程中途退出时记录停在 `executing`，不会被再次批准。

## 3. 参数的保存与脱敏

- 写和高危工具的参数经工具的 Pydantic 模型校验并规范化后保存，执行时只用存下的这份，AI 之后改不了。
- `arguments` 列只在待审批期间保存完整参数；处理完（执行结束、拒绝、过期）就清空为 NULL。
  读工具和 `denied` 记录不保存完整参数。
- 工作流里标为敏感（`sensitive: true`）的触发参数（例如邮箱、单号）：
  - 取值用 `CREDENTIAL_ENCRYPTION_KEY` 加密后再存（AES-256-GCM，绑定到这条记录和参数名），
    数据库与备份里没有明文；执行时解密；
  - 展示版本 `arguments_display` 里是 `***`，接口只返回展示版本；
  - `denied` 的触发请求没有查过哪些参数敏感，展示版本里全部参数值都是 `***`。
- 去重用的 `arguments_hash`：配置了凭证密钥时是 HMAC-SHA256（密钥由凭证密钥派生，用途标签
  `ai_tool_arguments`），敏感参数的取值不能用字典从哈希反推；没配置密钥时是 SHA-256
  （这时也不可能有带敏感参数的工作流，因为 n8n 连接本身就需要凭证密钥）。
  轮换凭证密钥后，进行中的待审批可能与新请求算出不同的哈希，最多多出一条待审批，不影响正确性。

## 4. 审批

- `approve` / `reject` 需要会话、CSRF 与 `If-Match`；`If-Match` 不等于当前 `version` 返回 409
  `VERSION_CONFLICT`；不是待审批或已过期返回 409 `EXECUTION_NOT_PENDING`（顺带把过期的记录改成 `expired`）。
- 只有 owner / admin 能处理（契约第 6 节）；member 及以下在列表里只看到 `acting_user_id` 是自己的记录，
  `pending_count` 是全工作空间的待审批数。
- 高危操作（`delete_task`、`delete_calendar_event`、`trigger_automation`）要带当前用户的密码：
  没带 403 `STEP_UP_REQUIRED`，错误 403 `STEP_UP_FAILED`。密码错误按用户计数，15 分钟内 5 次后返回
  429 `RATE_LIMITED`（带 `Retry-After`），与登录失败的限流共用 `FailureLimiter`。
- 每个实例最多 20 条待审批，超过返回工具错误 `TOO_MANY_PENDING`；待审批 15 分钟没人处理就过期
  （worker 每分钟一次，发起新请求和处理审批时也会顺带检查）。

## 5. 版本保护

- 修改、完成、删除任务和删除日程时，创建审批那一刻记下目标的版本（`target_version`）；执行时把这个版本交给
  领域服务，版本已变就以 `failed` / `VERSION_CONFLICT` 结束，不覆盖别人的修改。
- 新建类操作（新建任务、日程、书签）和标记通知已读没有目标版本。

## 6. 结果未知

- 领域服务返回 `RESULT_UNKNOWN` / `WRITE_UNKNOWN` 或 504 时记 `unknown`，不自动重试，提示到对应页面确认。
  沿用 V1 写操作“未知完成状态”的处理方式。

## 7. `summary`

- 由工作台按参数和查到的目标生成（例如“删除任务「写周报」”），不采用 AI 给的文字；
  标题截断到 60 个字符。

## 8. 后续批次

- 第 2 批：`POST /mcp`（MCP Python SDK，见 OC-0 ADR 第 7 节）、实例凭证认证、读工具（结果包在
  `untrusted` 里）、按实例限流、`get_action_status`，读工具与 `denied` 也写记录。
- 第 3 批：审批卡片经 `ws_notice` 发到“审批通知”会话，同时写通知 `approval_requested`；`source` 的推断。
- 审批中心与活动页（前端）在 T-0056 里做。
