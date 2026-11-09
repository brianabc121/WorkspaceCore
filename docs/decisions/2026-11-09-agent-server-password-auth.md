# 2026-11-09 OpenClaw 服务器改用保存的加密登录密码（T-0178 ~ T-0183）

- 状态：accepted（人类 2026-11-09 在对话中要求按 `docs/v2/agent-servers-redesign/` 全面落实“智能体服务器 / OpenClaw 智能体”
  改造：服务器用 IP、SSH 端口、账号、密码添加，取消 SSH 密钥登录；技术细节由 claude 决定）
- 取代（只限 OpenClaw 服务器）：
  - `2026-11-03-openclaw-ssh-provisioning.md` 第 1 节“SSH 密码用完怎么办 → 只用一次，换成专用密钥”、第 5.1 节的
    “密码阶段 + 专用密钥”、第 6.2 节“吊销专用密钥”；
  - `docs/v2/MULTI_OPENCLAW_PLAN.md` 的 D7（每台服务器一把专用密钥）与 `2026-11-07-multi-openclaw-verification.md` 里
    “准备一次后不再用密码”的结论。
  旧 ADR 与方案保留原文作为历史，不改写；本文件是当前依据。
- 不变：SSH **主机身份校验**（先探测、owner 核对指纹、确认后固定公钥，变了就拒绝）、OpenClaw 的 mTLS 客户端证书与实例令牌、
  工作台密钥库（`agent_secrets`）与 CredentialBox、运行服务器（Runner，`runner_ssh_targets`）的 SSH 流程、
  运维敏感动作的**工作台账号**二次认证（`body.password` 仍是工作台登录密码，不是 SSH 密码）。
- 关联：`docs/v2/agent-servers-redesign/docs/superpowers/specs/2026-11-09-agent-servers-openclaw-contract.md`（接口、状态与迁移约定）、
  `services/api/app/domain/agent_server_credentials.py`、`agent_server_ssh.py`、`agent_servers.py`、迁移 `0054`。

## 1. 决定

| 问题 | 决定 |
|---|---|
| 服务器怎么登录 | 保存 owner 填写的 SSH 登录密码（加密），初始化、部署、卸载、运维全程用它登录；**不再生成、安装或使用新的 SSH 客户端密钥** |
| 密码怎么存 | CredentialBox（AES-256-GCM，`enc:v1:<key_id>:…`），用途 `agent_server_password:<agent_ssh_targets.id>`；只接受 `enc:v1:` 引用（`env:` 引用不是用户保存的密码） |
| 谁能看到 | 任何接口都不返回明文或密文引用；只返回 `password_set`、`credential_revision` 与连接状态 |
| 密码被换过 | `credential_revision` 每次设置 / 替换加 1（主密钥轮换不加）；后台任务记下开始时的版本，登录前核对，变了就停（`AGENT_SERVER_CREDENTIAL_CHANGED`） |
| 首次连接 | 添加时只做密钥交换、读主机公钥（不登录）；**指纹确认之前密码不会发给服务器** |
| sudo | 复用同一个登录密码，只经标准输入交给 `sudo -S -p '' -k`；需要另外的 sudo 密码或交互式 MFA 的环境明确提示不支持 |
| 旧服务器 | 旧密码从未保存，不能从专用私钥还原：迁移后 `password_set=false`，提示补录；已有实例不停、不重装；旧公钥经密码登录后**定向**删除本工作台那一行（T-0179） |

## 2. 数据（迁移 0054，加法）

`agent_ssh_targets` 新增：

| 字段 | 类型 / 缺省 | 说明 |
|---|---|---|
| `login_password_ref` | text，null | 密文引用；旧记录 null（不生成假密码） |
| `credential_revision` | int，默认 0，`>= 0` | 设置 / 替换密码加 1；新添加的服务器是 1 |
| `last_connection_at` | timestamptz，null | 最近一次显式连接检测完成的时间 |
| `last_connection_reason` | text，null | 白名单：`connected` / `auth_failed` / `unreachable` / `host_key_changed` / `credential_unreadable` |
| `legacy_key_cleanup_state` | text，默认 `not_needed` | `not_needed` / `pending` / `done` / `manual_required`；有 `client_key_ref` 或 `client_public_key` 的旧记录回填 `pending` |

`agent_deployments.runtime_installed`（bool，null）：部署成功 true、卸载容器保留目录 false；旧记录 null（未知），不凭
`installed_at` 推断。写入在 T-0180 完成，之前的新部署也是 null（成功数保守地给 null + `deployment_counts_partial`）。

`client_key_ref` / `client_public_key` 暂留为迁移兼容列：新逻辑不读取旧私钥来登录，显式退役成功后才清空；本轮不 DROP。
加密密钥轮换（`app.commands.rotate_credentials`）已登记 `agent_ssh_targets.login_password_ref`：只重新加密，不改
`credential_revision`、`version` 与 `updated_at`。

## 3. 接口（`/api/v1`，owner；X-Workspace-Id、CSRF、`version` 乐观并发沿用）

| 接口 | 说明 | 状态 |
|---|---|---|
| `POST /agents/servers` | `{name?, host, port=22, username, login_password}`：名称省略用 `host:port`（IPv6 `[addr]:port`，≤ 100 截断）；只探测主机公钥并加密保存密码，不登录 | T-0178 已实现 |
| `PUT /agents/servers/{id}/credentials` | `{version, login_password}`：替换密码、revision + 1、连接状态回到 unknown；保留 `prepared_at` 与实例；同机有任务 / 运维动作 `409 AGENT_SSH_JOB_RUNNING`（原密码不变） | T-0178 已实现 |
| `POST /agents/servers/{id}/test-connection` | `{version}`：固定指纹 + 密码登录，只读检查环境（`inspect.sh`），需要时用同一个密码验证 sudo；登录结果写进 `ssh.connection_status`，环境问题在 `warning`；不装 Docker、不建容器、不写 authorized_keys；与探测共用每分钟 10 次限流 | T-0178 已实现 |
| `PUT /agents/servers/{id}/address` | 加必填 `login_password`；有部署记录 `409 AGENT_SERVER_HAS_DEPLOYMENTS`；重新探测、取消确认、清空初始化与连接状态 | T-0178 已实现 |
| `POST /agents/servers/{id}/prepare` | `{version}`（不再收密码）；没保存密码 `409 AGENT_SERVER_CREDENTIAL_REQUIRED`，解不开 `409 AGENT_SERVER_CREDENTIAL_UNREADABLE` | T-0178 改输入；任务改全程密码在 T-0179 |
| `POST /agents/servers/{id}/legacy-key-cleanup` | `{version}`：密码登录、只删本工作台那一行公钥 | 契约已定，501，T-0179 |
| `POST /agents/deployments` | 一次点击新建并部署，`request_id` = `Idempotency-Key` | 契约已定，501（只先校验两者一致），T-0180 |
| `GET /agents/deployments/requests/{request_id}` | 部署请求收据 | 契约已定，501，T-0180 |
| `GET /agents/overview` | 智能体总览（summary / items / next_cursor / generated_at） | 契约已定，501，T-0180 |

服务器的四个写接口（添加、替换密码、测试连接、更换地址）接受**可选**的 `Idempotency-Key`：记录只在成功时与业务写入同一事务
提交，内容只有操作名与服务器 ID；重放返回这台服务器**现在**的详情（`Idempotent-Replayed: true`），不缓存响应正文、不再连接服务器；
请求指纹里的密码换成 HMAC（由工作台加密密钥派生的子密钥 `agent-server-idempotency`），幂等表里没有可撞库的明文摘要。

`AgentDeployIn.password` 标为已弃用并**忽略**（不使用、不回显）；`ServerSshOut.client_key_installed` / `client_key_fingerprint`
标为已弃用（迁移兼容），不参与是否可部署的判断。`AgentOpsState.reason` 增加 `credential_missing` / `credential_unreadable` /
`host_key_unconfirmed`（T-0179 起使用），`key_missing` 已弃用。

## 4. 状态口径（只读本地记录，GET 不连 SSH）

- `connection_status`：没保存密码 `credentials_required`；密文所用的密钥不在当前配置里 `credential_unreadable`（只看 key_id，不解密）；
  否则按 `last_connection_reason`，没检测过（或刚换过密码、地址）是 `unknown`。
- `initialization_status`（kind=auto 为 null，“不适用”）：最近一次初始化任务 running → `initializing`；最近的任务失败 / 中断、
  且 `prepared_at` 早于它 → `failed` / `interrupted`；有 `prepared_at` → `ready`；其余 `not_initialized`。
- `deployable` / `deploy_block_reason`（第一个不满足的）：`not_ssh` → `credentials_required` → `credential_unreadable` →
  `host_key_unconfirmed` → `host_key_changed` / `auth_failed`（最近一次检测）→ 初始化未就绪（`not_initialized` / `initializing` /
  `initialization_failed` / `initialization_interrupted`）→ `busy`（同机有 SSH 任务或运维动作）。`unknown` 不阻止，但实际部署仍做预检。
- 部署汇总：`managed_deployments` = 部署记录数；`successful_deployments` = `installed_at` 有值且 `runtime_installed=true`，
  有 `installed_at` 有值而 `runtime_installed` 为 null 的旧记录时为 null 并 `deployment_counts_partial=true`；
  `allocated_mem_limit_mb` / `allocated_cpus` 是有部署记录的实例上限之和，任一不限制时为 null 且 `has_unlimited_limits=true`。
- `AgentServerList.summary` 基于当前工作空间的完整列表：`server_count`、`ready_count`（ssh 且初始化就绪，不等于在线）、
  `successful_deployments`（任一服务器 partial 时 null）、`not_initialized_count`（只计 ssh）、`needs_credentials_count`、
  `deployment_counts_partial`、`generated_at`。
- `ServerAgentRow.deployment_state`：没有部署记录 `associated`；最近的部署任务进行中 / 失败 / 中断 → `deploying` / `failed` /
  `interrupted`（旧的成功部署重部署失败时同样标明最新失败）；`runtime_installed=false` → `retained`；有 `installed_at` → `deployed`。
  `deployment_project` 是平台确定的 Compose 项目名 `myworkspace-openclaw-<实例短号>`，不是容器名；没有真实检测就不输出容器状态。

## 5. 登录策略与 asyncssh 核验（锁定 2.24.1）

`app/integrations/ssh.py` 的 `connect_password_only` 只给 OpenClaw 服务器用（Runner 仍用原来的 `connect`）。参数在锁定版本的
`SSHClientConnectionOptions.prepare`（`asyncssh/connection.py`）上逐一核对过：

| 参数 | 作用 |
|---|---|
| `client_keys=None`、`public_key_auth=False` | 文档：“If this argument is explicitly set to None, client public key authentication will not be performed”；不读 `~/.ssh/id_*` |
| `agent_path=None`、`agent_forwarding=False` | 文档：“If this argument is explicitly set to None, an ssh-agent will not be used” |
| `host_based_auth=False`、`gss_host=None`、`gss_kex=False`、`gss_auth=False` | 不做基于主机的认证与 GSS（`gss_host=None` 时 GSS 完全不做） |
| `preferred_auth="keyboard-interactive,password"`、`kbdint_auth=True`、`password_auth=True` | 只用密码（含只有一个密码提示的 keyboard-interactive） |
| `disable_trivial_auth=True` | 服务器不真正校验凭证（none 认证、没有提示的 keyboard-interactive）就当作失败 |
| `known_hosts=([固定公钥], [], [])`、`config=[]` | 只接受确认过的主机公钥；不读 `~/.ssh/config` |

实测（`tests/test_agent_server_credentials.py::test_password_only_never_falls_back_to_keys`）：运行环境的 `HOME` 下放一把服务器信任的
默认私钥、设置 `SSH_AUTH_SOCK`，保存的密码错误时登录失败（`auth_failed`），服务器只收到 `password` 尝试，没有任何 `publickey` 尝试。

## 6. 迁移与回滚边界

- 0054 只加列、加约束与回填 `legacy_key_cleanup_state`；不清空、不删除旧私钥 / 公钥，不改 `prepared_at`、`server_id`、部署与实例。
- `downgrade` 删除新列与约束，旧数据原样保留；但**不能恢复**已经主动退役的远端公钥或已清空的私钥引用（T-0179 的显式动作），
  也会丢失已保存的密码密文。生产回滚前先备份（`make backup`），评估版本兼容；降级不是“连接迁移”的撤销按钮。
- 临时库验证：`tests/test_agent_server_password_migration.py`（0053 → 0054 断言旧数据 → 0053 → 0054）。

## 7. 威胁与缓解

| 威胁 | 缓解 |
|---|---|
| 数据库泄露得到 SSH 密码 | 只存 AES-256-GCM 密文，密钥在 `CREDENTIAL_ENCRYPTION_KEY`（不在库里）；用途绑定到记录，密文挪到别的服务器解不开；支持轮换 |
| 接口、日志、审计、事件、幂等记录泄露 | 只写不读；422 不回显输入；审计只记 `login_saved` / `auth_revision`（`password`、`credential` 类键名本身也会被审计脱敏）；事件只有 topic；幂等记录只有服务器 ID；请求指纹用 HMAC；测试逐表搜索明文 |
| 中间人 / 连错机器 | 指纹确认前不发送密码；之后只接受固定的主机公钥，变了记为 `host_key_changed` 并停止；更换地址要重新探测、确认，有部署记录时拒绝（不把原实例挂到新机器） |
| 回退到别的凭证 | 显式关闭公钥、agent、GSS、主机认证，`disable_trivial_auth`；旧专用私钥不作为登录回退 |
| 密码中途被换、并发改连接信息 | 替换密码 / 更换地址锁服务器与目标行并检查同机任务；后台任务按 `credential_revision` 解密；测试连接前后比对连接信息，变了不写结果 |
| 被当作暴力破解远端密码的工具 | 测试连接与探测共用每个用户每分钟 10 次的限流；只有 owner 可用；每次检测写审计 |
| sudo 密码进入命令行或日志 | 只经标准输入；任务日志对密码做替换（`JobRecorder`）；测试连接不记录命令输出 |
| SSH 密码与工作台账号密码混用 | 两个字段、两个用途：运维二次认证仍校验工作台账号密码；服务器密码不进接入包、不注入容器、不授权给智能体 |

## 8. 过渡期（T-0178 合并到同一交付分支、T-0179 之前）仍走旧路径的地方

- `agent_ssh_install.Installer.run`：初始化 / 部署任务仍是“密码阶段 + `authorize_key` 装专用公钥 + 专用密钥重连”。T-0178 只把密码
  来源从请求体换成保存的密文（接口层 `_saved_password` 解密后交给协程）；部署时旧专用私钥还在就仍用它登录。
- `Uninstaller.uninstall`、`AgentServerService.undeploy`、`revoke` / `_remove_remote_key`、`agent_ops.py`（`unavailable_reason`、
  `_connect` 用 `client_key_ref`）仍用旧专用密钥。
- 这些在 T-0179 改为 `agent_server_ssh.connect_agent_server`（全程密码、按 revision 解密），旧 `revoke` 返回
  `410 AGENT_SSH_KEY_AUTH_RETIRED`，新增 `legacy-key-cleanup`。新 API 与新页面整体配套后才合并（方案 README），
  不单独合并 T-0178。

## 9. 与约定的偏差

- `AgentServerOut.initialization_status` 对 kind=auto 返回 null（约定只列了五个值，页面显示“不适用”）。
- `deploy_block_reason` 用固定枚举（约定写的是 `str | null`），便于前端强类型。
- 服务器写接口的 `Idempotency-Key` 是可选的（现有服务器写接口以 `version` 乐观并发为主）；`POST /agents/deployments` 必须带。
- 测试连接不改服务器的 `version`（检测结果是观测，不是配置）；替换密码、更换地址仍加 1。
