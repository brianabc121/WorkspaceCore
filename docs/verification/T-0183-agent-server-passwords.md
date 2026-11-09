# 核验记录：智能体服务器保存的登录密码、一次点击部署与旧公钥退役（T-0183）

日期：2026-11-09。依据：`docs/decisions/2026-11-09-agent-server-password-auth.md`（第 8 ~ 10 节是最终实现边界）、
`docs/v2/agent-servers-redesign/`（设计规范、接口约定、计划 Task 6）。
覆盖 T-0178 ~ T-0182 的后端与两个页面，在真实 sshd + 真实 Docker + 两个真实 OpenClaw 上验收。

## 1. 在哪里跑

CI `.github/workflows/ci-infra.yml` 的 `agents` job（GitHub `ubuntu-latest`，以 root 运行验收脚本），在“接入入口自动配置验收”之后：

1. **SSH 自动部署验收准备**：本机另起一个 sshd（`ListenAddress` 是 `AGENT_PUBLIC_ADDRESS`，端口 2222），专用账号 `mwdeploy`：
   密码登录（`PasswordAuthentication yes`、`KbdInteractiveAuthentication no`）、在 sudo 组且 sudo 要密码、**不在 docker 组**；
   密码由 `openssl rand -hex 16` 生成并在日志里屏蔽。环境变量 `SSH_TARGET_HOST` / `SSH_TARGET_PORT` / `SSH_TARGET_USER` /
   `SSH_TARGET_PASSWORD` / `SSH_HOST_KEY_PUB`。
2. **接口验收**：`sudo -E python3 tests/e2e/agent_ssh_acceptance.py`
3. **浏览器验收**：`node tests/e2e/agent_ssh_browser.cjs`（同一个 sshd 与账号，这时账号已在 docker 组）
4. **回归**：`node tests/e2e/runner_browser.cjs`（运行服务器仍用专用密钥，认证不变；同一个账号，接口验收结束时
   `authorized_keys` 已恢复成测试前的样子）；`node tests/e2e/sprint6_browser.cjs`（侧栏名称、“手动登记 / 接入”、详情地址）。

本地没有 sshd + OpenClaw 环境，脚本只做了静态检查（第 4 节），**真实结果以 CI 为准**。

## 2. 接口验收覆盖（`tests/e2e/agent_ssh_acceptance.py`）

| # | 检查 |
|---|---|
| 1 | 添加服务器只传 host / port / username / login_password（不传 name）：201，名称是 `host:port`；`ssh.auth_method=password`、`password_set=true`、`credential_revision=1`、`connection_status=unknown`、`legacy_key_cleanup_state=not_needed`；响应正文里没有密码；指纹与 sshd 的主机公钥一致；数据库 `login_password_ref` 是 `enc:` 密文、不含明文；`deploy_block_reason=host_key_unconfirmed` |
| 2 | 确认指纹之前：test-connection、prepare、`POST /agents/deployments` 都是 `409 AGENT_SSH_HOST_KEY_UNCONFIRMED`，实例数仍是 0 |
| 3 | 确认指纹 → test-connection 200、`connected`、没有环境警告（同一个密码能 sudo）；`authorized_keys` 与测试前**完全相同**（测试前可能不存在） |
| 4 | `PUT credentials` 换成错误密码（第 2 版、`unknown`）→ test-connection 200 + `auth_failed`、`deploy_block_reason=auth_failed`；这时一次点击部署 `409 AGENT_SERVER_CONNECTION_FAILED` 且不建实例；改回正确密码（第 3 版）→ `connected` |
| 5 | 初始化（body 只有 version，202）→ 成功；步骤 connect / inspect / docker / reconnect / workspace / verify，docker 一步经 sudo 把账号加入 docker 组（`done`），reconnect `done` 或 `skipped`；`~/myworkspace-openclaw` 存在、属于这个账号、没有实例子目录；`authorized_keys` 不变；`initialization_status=ready`、`deployable=true`、`prepared_at` 有值。再初始化一次：成功、docker 与 reconnect 跳过、不建实例 |
| 6 | 一次点击部署甲（`request_id` = `Idempotency-Key`，内存 3072 MiB、1.5 核）→ 202，外网 / read / member，上限进了实例，任务步骤里没有 `authorize_key`；同一个 request_id 同样的内容立即重放 → 202、同一个实例、`Idempotent-Replayed: true`；收据 `accepted`、实例与任务相同；甲部署中用另一个 request_id 部署乙到同一台 → `409 AGENT_SSH_JOB_RUNNING`、实例数仍是 1、那个请求的收据 `failed`；部署开始后给甲授权密钥（用响应里的 instance.version）；甲部署成功，步骤是 connect … reflection，connect 用保存的登录密码，部署记录 `runtime_installed=true` |
| 7 | 部署乙（新 request_id、不限资源）成功；原有多实例检查：心跳与归属、各自三个容器、目录 700 与 uid 1000、各自的网络命名空间、甲有资源上限乙没有、`authorized_keys` 与测试前相同（没有平台公钥）、每个实例六行 crontab、照 crontab 上报、服务器详情的整机与各智能体负载；运维：状态、日志脱敏、隧道日志、立即上报、不带密码 `STEP_UP_REQUIRED`、**填 SSH 密码 `STEP_UP_FAILED`**、填工作台密码重启与重新拉起、实例级清理镜像 422、服务器级查看全部容器与清理镜像、乙的容器不变、审计 8 个动作不含输出 |
| 8 | `GET /agents/overview` 的 `summary.total=2`，两行都归到这台服务器、带各自的 Compose 项目名；`GET /agents/servers` 的 summary（1 台、1 台就绪、部署成功 2、不是部分统计、没有待初始化与待补密码），这台服务器部署记录 2、成功 2、含不限制；服务器详情两行都是 `deployed` |
| 9 | 旧接口 `POST /agents/instances/{id}/deploy`（只有 server_id 与 version）重新部署甲：成功，插件跳过，复盘任务只更新，乙的容器 ID 不变，定时任务不重复 |
| 10 | 卸载甲、保留数据：`runtime_installed=false`、`installed_at=null`，容器删了、目录还在、定时任务删了，乙照常；服务器详情甲 `retained`、乙 `deployed`、部署记录 2、成功 1。再卸载甲、删除目录：没有部署记录、未归属，目录与定时任务只删甲的，乙照常，甲的运维 `not_deployed` |
| 11 | 乙还有部署记录：删除服务器 `409 AGENT_SERVER_HAS_DEPLOYMENTS`；卸载乙后 `~/myworkspace-openclaw` 空了一起删掉；全程 `authorized_keys` 不变 |
| 12 | 旧公钥退役：生成两把临时 ed25519 公钥（“工作台旧公钥”与“其他用户的公钥”）追加到 `authorized_keys`，SQL 把这台服务器的 `client_public_key` 设为旧公钥、`client_key_ref` 设占位、`legacy_key_cleanup_state=pending`；服务器显示 pending 与旧公钥指纹；删除服务器 `409 AGENT_SERVER_LEGACY_KEY_PENDING`；`POST /revoke` → `410 AGENT_SSH_KEY_AUTH_RETIRED` 且 `authorized_keys` 没动；`POST /legacy-key-cleanup` → 200、`done`、`client_key_installed=false`、指纹清空、`connected`、没有 warning；`authorized_keys` 里旧公钥那一行没了、其他用户那一行与原有的行都在；数据库两列都清空；审计 `agents.server.legacy_key_cleaned` 一条 |
| 13 | 全程结束：正确与错误的 SSH 密码都不在 `agent_ssh_jobs`、`agent_ssh_targets`、`agent_servers`、`agent_deployments`、`agent_ops_actions`、`audit_logs`、`idempotency_requests` 里；删除服务器 204，之后 404；`authorized_keys` 恢复成测试前的样子（不影响之后的浏览器与运行服务器验收） |

## 3. 浏览器验收覆盖（`tests/e2e/agent_ssh_browser.cjs`）

- 侧栏有“智能体服务器”“OpenClaw 智能体”；`/agent-servers` 点“添加服务器”，只填 IP、SSH 端口（默认 22）、登录账号、登录密码（名称默认空）→ “测试并保存”；
  表单收起；页面显示的指纹与 sshd 的一致、给出核对命令；勾选核对前不能确认 → “确认指纹并测试连接” → “连接正常”。
- 服务器名称是 `host:port`；列表“待初始化 / 连接正常”；详情“密码 · 已加密保存”，没有专用密钥 / 吊销按钮。
- “初始化服务器”：跟着 6 步直到“服务器初始化完成”，第一步是“用保存的密码登录服务器”，没有装公钥；出现“前往部署智能体”，链接带 `deploy=1&server_id=`。
- 旧链接 `/agents?tab=servers&server=<id>` 被 replace 到 `/agent-servers?server=<id>`。
- “前往部署智能体”打开部署抽屉并预选这台服务器；抽屉里没有密码 / IP / 账号输入，显示锁定版本；填名称“开始部署”，跟着 9 步直到“部署完成，实例已连上工作台”。
- 智能体列表里有它（所属服务器链接、部署成功）；详情概览“平台自动部署（Docker Compose）”与所在服务器；卡片“最近在线”有时间。
- 运维：查看状态、立即上报、重启 OpenClaw（输**工作台**登录密码）；实例负载；服务器详情“已部署 OpenClaw”里有它（已部署、项目名、链接）、
  “平台部署记录 1 条 · 部署成功 1”、整机份额、各智能体趋势、查看全部容器；390px 无横向滚动。
- 卸载（删除目录）→ 移除服务器 → 两页 390px 无横向滚动。
- 安全：SSH 密码只出现在 `POST /api/v1/agents/servers` 这**一个**请求里；不在页面、接口响应、localStorage / sessionStorage；
  接口响应里没有 `login_password_ref`；工作台密码只在登录与确认重启两个请求里；浏览器只访问工作台地址。
- 保留 `SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1`：部署停在插件一步时继续验收卸载与移除服务器。

## 4. 本地静态检查（2026-11-09）

| 命令 | 结果 |
|---|---|
| `python3 -m py_compile tests/e2e/agent_ssh_acceptance.py` | 通过 |
| `ruff check tests/e2e/agent_ssh_acceptance.py`（仓库没有给 `tests/e2e` 的 ruff 配置，用默认规则） | 只有 EXE001（与同目录其他验收脚本一样带 shebang、文件 644） |
| `node --check tests/e2e/agent_ssh_browser.cjs`、`node --check tests/e2e/sprint6_browser.cjs` | 通过 |

## 5. 对照代码发现的问题（2026-11-09 写本记录时，未改业务代码）

1. **初始化的最终检查在“没有域名”的接入入口上会失败**（会挡住 CI 的接口与浏览器验收）：
   `services/api/app/domain/agent_ssh_install.py` 第 675 行 `_verify` 用 `AGENT_PUBLIC_URL` 的主机名去连接入地址；
   `deploy.sh connect` 在没有域名时用内部主机名 `agent-gateway.myworkspace.internal`，它只在接入包隧道容器自己的 hosts
   （`tls/hosts`，映射到 `AGENT_PUBLIC_ADDRESS`，`app/agent_bundle/__init__.py` 第 264 行）里能解析，服务器本机解析不了 →
   `verify.sh` 输出 `ingress=no` → 第 692 行抛 `ingress_unreachable`，初始化失败。CI 的 agents job 正是这种配置。
   最小修复：`_verify` 里设置了 `AGENT_PUBLIC_ADDRESS` 时改连 `(agent_public_address, 端口)`（与接入包隧道的映射一致）。
2. **保留数据卸载后运维仍显示可用**：`services/api/app/domain/agent_ops.py` 第 224 行 `unavailable_reason` 只看部署记录与
   `install_dir`，`runtime_installed=false`（容器已卸载、目录保留）时“重新拉起全部服务”会在保留的目录里 `docker compose up -d`
   把容器拉回来，而部署记录仍是 `retained`、定时任务已删除。建议 `runtime_installed is False` 时返回 `not_deployed`（或新的原因）。
3. **前端缺几个新错误码的文字**：`apps/web/src/features/agents/ssh.ts` 第 60 ~ 88 行的 `sshError` 没有
   `AGENT_SERVER_LEGACY_KEY_PENDING`、`AGENT_SERVER_NO_LEGACY_KEY`、`AGENT_SERVER_CONNECTION_FAILED`，落到 `agentError`
   显示“请求失败，请稍后重试。”。例如旧公钥待退役的服务器点“移除服务器”（`ServerInitializationPanel.vue` 第 147 行，按钮没有因此禁用）
   只看到这句通用提示。

**处理（2026-11-09，同一 PR）**：三条都已修复——
1. `_verify` 在设置了 `AGENT_PUBLIC_ADDRESS` 时按这个地址与入口端口检查（`test_verify_checks_the_public_address_when_the_ingress_name_has_no_dns`）；
2. `unavailable_reason` 在 `runtime_installed is False` 时返回 `not_deployed`（`test_retained_deployment_offers_no_instance_ops`）；
3. 前端补上三个错误码的文字；旧公钥待退役时“移除服务器”禁用并说明原因（`agent-server-redesign.test.ts`）。

## 6. 结果

CI 运行：ci-infra [42592666997](https://github.com/brianabc121/WorkspaceCore/actions/runs/42592666997)（提交 `3387bf6`，与之后的 `264dacd` 只差一个开发者总览测试文件的时间修复，业务代码相同），
agents 任务 129077765983 成功（09:20–10:00 UTC）；同一次提交的 ci-api、Web CI、ci-security、collab 也全部成功。

| 步骤 | 结果 |
|---|---|
| 多实例端到端（含场景 26 运行服务器回归） | 成功（09:28–09:46） |
| `agent_ssh_acceptance.py`（真实 sshd + Docker，一台服务器两个真实 OpenClaw） | “全部通过”（09:48–09:56）：密码加密保存（库里是 `enc:v1:` 密文）、指纹确认前测试连接 / 初始化 / 部署都 409、错误密码 `auth_failed` 且部署被拒不建实例、初始化 6 步（connect / inspect / docker / reconnect / workspace / verify，最终检查连到 `10.1.1.123:9443`）、再初始化跳过 Docker、一次点击部署甲（同一 request_id 重放返回同一实例，收据 accepted）、甲部署中部署乙 409 同机互斥（收据 failed、没有实例）、甲乙都连上工作台且各有自己的网络命名空间与资源上限、全程 authorized_keys 不变、运维二次认证只认工作台密码、重新部署跳过已装插件、保留数据卸载后 runtime_installed=false 且不再提供运维、旧公钥退役只删那一行、密码不在任务 / 服务器 / 部署 / 运维 / 审计 / 幂等记录里 |
| `agent_ssh_browser.cjs` | “全部通过（页面无错误）”（09:56–09:59）：四项添加、核对指纹后测试连接、初始化 6 步、旧链接 `/agents?tab=servers` 换到 `/agent-servers`、部署抽屉预选服务器且没有 IP / 密码输入、部署 9 步到上线、运维、卸载、移除、390px 无横向滚动、密码只随“添加服务器”一个请求发出 |
| `runner_browser.cjs`（Runner 仍用专用密钥流程） | “全部通过（页面无错误）”（09:59–10:00） |

第 5 节的三处问题已在 `5c3ae8e` 修复，CI 的验收在修复之后运行。
