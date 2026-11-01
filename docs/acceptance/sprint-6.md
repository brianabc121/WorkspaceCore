# Sprint 6 验收报告（S6-01 ~ S6-06）

- 验收人：claude（S6-04 ~ S6-06 为协作任务 T-0046，S6-01 ~ S6-03 为 T-0059；按 D-0003 负责每个 Sprint 的联调与验收；codex 暂停期间按 D-0007 独立完成所有任务）
- 日期：2026-10-31（S6-04 ~ S6-06）、2026-11-01（S6-01 ~ S6-03，第 3.4 节）
- **结论**：
  - **S6-01 ~ S6-06 全部通过**。规范修订后的 Sprint 6 DoD 四条全部通过：无审批不能执行高危操作；攻击性内容（包括其他 OpenClaw 的群消息）无法绕过工具策略；能从备份恢复（含 OpenIM）并通过 Smoke Tests；定稿第 8 节的 14 个验收场景自动化通过。A14、A15 通过。这些都在 CI 里每次自动运行，Sprint 1 ~ 5 的验收也继续全部通过。
  - V2（S6-01 ~ S6-03）按 `docs/v2/TRANSFORMATION_PLAN.md` 实施：OpenClaw 多实例接入 + 基于 OpenIM 的管理员端 IM + 集中密钥库 + MCP 工具与审批。验证用的是真实的 OpenClaw 2026.9.36、openclaw-channel 0.3.3 与打过补丁的 OpenIM，模型用假模型（第 3.4 节、第 6 节）。
  - `docs/TASKS.md`：S6-04 ~ S6-06 在 T-0046 时已勾选，本次勾选 S6-01 ~ S6-03。
- 本 Sprint 合并的 PR：
  - [#63](https://github.com/brianabc121/WorkspaceCore/pull/63)：T-0041 核验、T-0042 备份恢复；
  - [#64](https://github.com/brianabc121/WorkspaceCore/pull/64)：T-0043 安全加固；
  - [#65](https://github.com/brianabc121/WorkspaceCore/pull/65)：T-0044 扫描、基准与完整冒烟，以及修复首页聚合在并发下超时；
  - [#66](https://github.com/brianabc121/WorkspaceCore/pull/66)：T-0045 发布文档与 digest 锁定、T-0048 修复首页刷新闪烁；
  - V2（S6-01 ~ S6-03）：方案与规范修订 [#72](https://github.com/brianabc121/WorkspaceCore/pull/72)、[#74](https://github.com/brianabc121/WorkspaceCore/pull/74)、[#75](https://github.com/brianabc121/WorkspaceCore/pull/75)；
    OC-0 核验 [#76](https://github.com/brianabc121/WorkspaceCore/pull/76)；OC-1 契约 [#77](https://github.com/brianabc121/WorkspaceCore/pull/77)；
    OC-2 实例、凭证、密钥库与接入入口 [#78](https://github.com/brianabc121/WorkspaceCore/pull/78) ~ [#80](https://github.com/brianabc121/WorkspaceCore/pull/80)；
    OC-3 OpenIM 部署与 IM 后端 [#81](https://github.com/brianabc121/WorkspaceCore/pull/81) ~ [#91](https://github.com/brianabc121/WorkspaceCore/pull/91)；
    OC-4 管理员端页面 [#92](https://github.com/brianabc121/WorkspaceCore/pull/92)、[#93](https://github.com/brianabc121/WorkspaceCore/pull/93)、[#101](https://github.com/brianabc121/WorkspaceCore/pull/101)；
    OC-5 MCP 工具与审批 [#94](https://github.com/brianabc121/WorkspaceCore/pull/94)、[#96](https://github.com/brianabc121/WorkspaceCore/pull/96) ~ [#100](https://github.com/brianabc121/WorkspaceCore/pull/100)；
    投影死锁修复 [#95](https://github.com/brianabc121/WorkspaceCore/pull/95)；OC-6 安全测试 [#102](https://github.com/brianabc121/WorkspaceCore/pull/102)；
    OC-7 验收 [#103](https://github.com/brianabc121/WorkspaceCore/pull/103)、[#104](https://github.com/brianabc121/WorkspaceCore/pull/104)、[#105](https://github.com/brianabc121/WorkspaceCore/pull/105)（含 T-0062 运维页与指标）。

**证据（V2）**：#105 合并后 main（`39f5b64`）上的 CI：

| 工作流 | 运行 | 内容 |
|---|---|---|
| `ci-infra` | [41313979474](https://github.com/brianabc121/WorkspaceCore/actions/runs/41313979474) | compose 任务：Sprint 1 ~ 5 接口级与浏览器验收、完整冒烟、性能基准、A15；agents 任务：构建 OpenIM 补丁镜像、接入入口验收、多实例端到端 14 个场景、A15 含 OpenIM |
| `ci-security` | [41313979639](https://github.com/brianabc121/WorkspaceCore/actions/runs/41313979639) | 依赖（含 openclaw-channel 发布包核对）、密钥泄露、镜像扫描（第三方清单含 OpenIM 依赖、OpenClaw、ghostunnel） |
| `ci-api` / `Web CI` / `collab` | 同一提交 | 后端 ruff / mypy / pytest（含 V2 的契约、安全矩阵、OC-6 安全测试）/ 迁移冒烟；前端 lint / typecheck / test / build / check:api |

**证据（S6-04 ~ S6-06）**：#66 合并前最新提交（`142a196`）上的 CI，7 项检查全部成功。

| 工作流 | 运行 | 内容 |
|---|---|---|
| `ci-infra` | [41185591245](https://github.com/brianabc121/WorkspaceCore/actions/runs/41185591245) | 全栈：Sprint 1 ~ 5 接口级与浏览器验收、完整冒烟、性能基准、A15、重启与数据卷检查 |
| `ci-security` | [41185591256](https://github.com/brianabc121/WorkspaceCore/actions/runs/41185591256) | 依赖、密钥泄露、容器镜像扫描 |
| `ci-api` / `Web CI` / `collab` | 同一提交 | 后端 ruff / mypy / pytest / 迁移冒烟；前端 lint / typecheck / test / build / check:api；协作检查 |

本机数据来自 claude 自己的 Docker 环境（D-0006）：共享的云端容器，单个 uvicorn 进程。

## 1. Sprint 6 DoD（`docs/DEVELOPMENT_SPEC.md` 第 13 节）

| 条件 | 结果 | 证据 / 原因 |
|---|---|---|
| 无审批不能执行高危操作 | **通过** | A14（第 2 节）：实例经 MCP 发起的写操作与高危操作只生成待审批记录，不执行；批准要 owner / admin，高危还要二次认证（密码，错误次数有上限）；参数被改过不执行；15 分钟过期；实例不能审批自己的请求，聊天里回复“同意”不算批准。`test_mcp_api.py`、`test_oc6_security.py`，多实例端到端场景 9 |
| 攻击性文档内容（包括其他 OpenClaw 的群消息）无法绕过工具策略 | **通过** | `test_oc6_security.py`：任务文本里的诱导指令、别的实例的群消息冒充来源，都只能生成待审批；多实例端到端场景 10（群里诱导甲去让乙删除数据，没有审批不会执行）、场景 11（实例之间的私聊被拒收） |
| 能从备份恢复（含 OpenIM）并通过 Smoke Tests | **通过** | A15（第 2 节）：`make backup` → 在隔离的空环境 `make restore-verify` → 迁移检查、冒烟检查、数据检查 → 原账号登录、显示名、首页布局与便签、日程、审计都与备份时一致；正在运行的栈不受影响。OpenIM 在运行时，快照一并包含 MongoDB 归档与 Garage 快照，恢复后各集合数量与对象数和备份时一致（agents 任务里在多实例端到端之后运行）。CI 每次运行 |
| 定稿第 8 节的验收场景自动化通过 | **通过** | `tests/e2e/agents_acceptance.py`，14 个场景全部通过（第 3.4 节） |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A14 AI 试图执行未经审批的删除 | V2：MCP 工具只返回 pending_approval，不执行，并记录；批准后按存下的参数执行 | **通过**：`test_mcp_api.py::test_a14_delete_through_mcp_only_creates_a_pending_record`；多实例端到端场景 9 用真实 OpenClaw 经 `tool_call` 调 `delete_calendar_event`：只生成待审批、管理员 IM 里出现审批卡片、二次认证批准后才删除、参数不同的另一条仍待审批 |
| A15 备份后在空环境恢复 | 数据 / 登录 / 首页配置通过 Smoke Test | **通过**（`tests/e2e/backup_acceptance.py`，CI 中 10 项全部通过），详见下表 |

A15 各项（CI 运行 41185591245）：

| 检查 | 结果 |
|---|---|
| 准备数据：改显示名、首页加便签并改布局、新建日程 | 通过 |
| `make backup`：生成加密备份并按保留策略清理 | 通过 |
| 备份是加密的：仓库里找不到 pg_dump 归档的文件头和本次写入的文字 | 通过 |
| 备份之后删掉日程、改掉便签，再执行 `make restore-verify`：在隔离环境恢复，迁移检查、冒烟检查、数据检查都通过 | 通过（数据检查：22 个用户、22 个工作空间、21 个仪表盘） |
| 恢复后用原来的账号和密码登录 | 通过 |
| 恢复后显示名、首页便签内容与布局都是备份时的 | 通过 |
| 恢复后日程还在（它是备份之后才删除的） | 通过 |
| 恢复后审计日志还在 | 通过 |
| 恢复演练不影响正在运行的栈（日程仍是删除后的状态） | 通过 |

本机另外执行过 `make restore-verify`：恢复约 50 个用户的数据库并通过检查，约 26 秒（T-0042）。

A15 的 OpenIM 部分（`backup_acceptance.py --expect-openim`，agents 任务）另加两项：快照包含 OpenIM（MongoDB 归档与 Garage 快照）；隔离恢复后 `msg`、`friend`、`group_member`、`conversation` 等集合的文档数与 Garage 对象数和备份时一致且不为空。本机在多实例验收留下的数据上跑过一次：user 39、friend 38、group 6、group_member 22、conversation 66、msg 29，Garage 对象 3，全部一致。

## 3. S6-04 ~ S6-06 逐项

### 3.1 S6-04 备份、隔离恢复与演练脚本（T-0041、T-0042）

| 项目 | 结果 | 证据 |
|---|---|---|
| 备份 | 通过 | `make backup`：`pg_dump -Fc` → `pg_restore --list` 确认归档完整 → 写入 restic 0.19.1 仓库（AES-256 加密）→ `check`。归档只在权限 700 的临时目录里短暂存在 |
| 保留策略 7 / 30 / 90 日 | 通过 | `forget --keep-within-daily 7d --keep-within-weekly 30d --keep-within-monthly 90d --prune`；用 27 个造出来的快照实测过保留结果（核验 ADR 第 3.3 节） |
| 异地 | 部分 | 后端可配置（S3、SFTP、REST 等，`BACKUP_REPOSITORY`）；只实测了本地目录仓库，见第 6 节 |
| 隔离恢复 | 通过 | `make restore-verify`：另一个 Compose 项目、另一个端口、自己的数据卷，不启动 Worker；`alembic upgrade head`、冒烟检查、数据检查；结束后删除。`--keep` 保留临时环境，便于核对 |
| 演练 | 通过 | A15 在 CI 里每次运行；部署文档第 14.4 节写了每天备份、每月演练 |
| 错误提示 | 通过 | 仓库密码不对、仓库不存在按 restic 退出码分别提示 |

### 3.2 S6-05 安全扫描、性能基准、完整端到端 Smoke Test（T-0043、T-0044、T-0048）

**安全加固（T-0043）**

| 项目 | 结果 | 证据 |
|---|---|---|
| 接口安全矩阵 | 通过 | `services/api/tests/test_security_matrix.py` 按 OpenAPI 枚举全部 `/api/v1` 接口，新增的接口自动纳入。检查 4 类情况：未登录 401；写请求没有 CSRF Token 403；非成员 404；viewer 写入 403 |
| 防 DNS 重绑定 | 通过 | 出站请求连接检查过的 IP，`Host` 与 TLS SNI 保留原主机名；熔断按集成计数（`tests/test_outbound_http.py` 7 项） |

**安全扫描（CI 运行 41185591256）**

| 扫描 | 结果 | 说明 |
|---|---|---|
| Python 依赖（pip-audit 2.10.1） | 通过 | No known vulnerabilities found |
| npm 依赖（npm audit） | 通过 | 高危与严重漏洞为 0。zod 升级到 4.6.7（公告 GHSA-mrhx-c7xc-m3w5）；vitest 升级到 4.1.12，修掉 tinypool 的 2 个严重漏洞。`apps/web` 剩 19 个中危，都在开发工具里，没有不破坏兼容的修复，按策略只报告 |
| 密钥泄露（gitleaks 8.30.1，整个 git 历史） | 通过 | no leaks found；5 处已知误报按指纹登记在 `.gitleaksignore`，每条写明理由 |
| 本仓库镜像（trivy 0.76.1） | 通过 | api 与 web 都是严重 0、高危 0。web 改用 nginx slim 变体，原来的 3 个漏洞没有了 |
| 第三方镜像 | 只报告 | caddy、grafana、n8n、postgres、restic、vikunja 有已修复版本的高危漏洞，修复要等上游发布新版本；prometheus、redis 为 0。发布前按 checklist 跟进，Caddy 优先 |

**性能基准**（`tests/perf/benchmark.py`）

数据量：200 条任务投影、40 个本周日程、50 条通知。表中是 P95，单位 ms。

| 接口 | 目标 | CI 并发 4 | CI 并发 16 | 本机并发 4 | 本机并发 16 |
|---|---|---|---|---|---|
| `/api/v1/me` | 300 | 13.4 | 84.5 | 29.9 | 109.1 |
| `/api/v1/workspaces` | 300 | 12.2 | 122.4 | 28.9 | 193.6 |
| `/api/v1/tasks` | 300 | 23.2 | 171.9 | 57.3 | 273.1 |
| `/api/v1/calendar/events` | 300 | 18.3 | 105.9 | 42.7 | 168.3 |
| `/api/v1/notifications` | 300 | 20.7 | 177.6 | 51.3 | 265.2 |
| `/api/v1/dashboard/overview` | 500 | 74.7 | 357.0 | 124.6 | **565.4** |

- 每组 200 次请求，都没有错误。
- CI 的结果全部在目标以内，判定时允许目标的 2 倍。
- 本机只有一组未达标：并发 16 时首页聚合 P95 为 565 ms。原因是单个 api 进程的吞吐上限（约每秒 39 次聚合），详见核验 ADR 第 8.6 节。并发 4 时远低于目标。
- 基准测试发现并修复了一个缺陷：并发下连接池被占满，首页各模块全部超时。修复前，并发 16 的 64 次请求里有 28 次如此；修复后 0 次。

**完整端到端冒烟**（`make smoke-full`，CI 22.7 秒）

| 检查 | 结果 |
|---|---|
| 登录 | 通过 |
| 全部读接口返回 200：27 个，按 openapi.json 枚举；需要具体资源 ID 的 2 个由各 Sprint 验收覆盖 | 通过 |
| 核心服务正常：API、Worker、PostgreSQL、Redis | 通过 |
| 导航里的 12 个页面加审计日志页都能打开，标题正确，没有页面错误、控制台错误与 5xx | 通过 |
| 实时连接（SSE）能建立 | 通过 |
| 写入往返：新建日程与首页便签 → 列表与首页可见 → 删除 | 通过 |
| 登出：回到登录页，接口返回 401 | 通过 |

T-0048：PR #66 的 CI 暴露出一个缺陷，已在同一个 PR 修复。
- 现象：首页后台重新拉取时，所有组件闪成“正在加载组件…”。
- 后果：Sprint 2 浏览器验收偶发失败。
- 修复后：CI 与本机连续 3 次都通过。

T-0049：#67 合并后，main 上的 ci-infra 又暴露出同类竞态，单独修复（[#68](https://github.com/brianabc121/WorkspaceCore/pull/68)）。
- **首页**：后台重新拉取期间，“编辑布局”按钮仍可点击，但点击后不生效。
  - 后果：main 上 Sprint 2 浏览器验收的“版本冲突”一步超时（运行 41187863842）。
  - 修复：重新拉取期间禁用这个按钮。
- **任务编辑器**（#68 第一次 CI 中 Sprint 3 浏览器验收发现，运行 41189858013）：后台重新拉取时，编辑器会锁定并静默丢掉提交；拿到新版本时，编辑器被重建，正在输入的内容会丢失。
  - 修复：以打开时的任务为编辑基准。用户没改就换成新版本；改了就保留输入并提示，保存时按原版本提交，真冲突由后端 409 发现。
- 本机验证：Sprint 2 浏览器验收连续 5 次通过，Sprint 3 浏览器验收连续 3 次通过。

### 3.3 S6-06 部署文档、版本表、升级 / 回滚说明、发布 checklist（T-0045）

| 项目 | 结果 | 位置 |
|---|---|---|
| 部署文档更新到 Sprint 6 | 通过 | `docs/deployment.md`：开头状态、第 5 节、第 14 节备份恢复、第 15 节扫描 / 冒烟 / 基准；去掉了过时的说明 |
| 版本表 | 通过 | `docs/versions.md`：所有镜像 tag + digest；原来只写 tag 的 5 个已锁定。另列关键依赖、许可证与核验来源 |
| 升级 / 回滚说明 | 通过 | `docs/deployment.md` 第 5 节：先构建、再用新镜像迁移、最后重建容器；迁移约定（先扩展后收缩、不可逆迁移由人类决定）；按情况回滚（downgrade 或从快照恢复）。命令都在本机实测过 |
| 发布 checklist | 通过 | `docs/release-checklist.md`：CI、扫描、版本锁定、备份演练、`.env` 与密钥轮换、性能、升级与验证、发布后 |
| 其他文档 | 通过 | README、`docs/architecture.md`、`docs/threat-model.md`、`docs/data-dictionary.md` 更新到 Sprint 6 |

### 3.4 S6-01 ~ S6-03 V2：OpenClaw 多实例 + OpenIM（T-0052 ~ T-0059、T-0061、T-0062）

**逐项**

| 任务 | 项目 | 结果 | 证据 |
|---|---|---|---|
| S6-01 | 实例登记与接入包 | 通过 | 实例页登记、授权密钥、下载接入包（凭证只出现一次）；接入包按锁定版本生成，`CHECKLIST.md` 是服务器端的检查清单。多实例端到端用真实的接入包启动两个 OpenClaw（场景 1）；验收中修复了接入包的两个问题（`_comment` 键、插件安装顺序，T-0059） |
| S6-01 | 内网与外网（双向证书）两种接入入口 | 通过 | agent-gateway 两个站点；外网入口客户端证书按指纹校验、可单独吊销；凭证绑定入口（场景 7）；`agent_ingress_acceptance.py` 13 项（含伪造请求头、吊销） |
| S6-01 | 集中密钥库（exec SecretRef） | 通过 | 实例经取密钥小程序领取授权给它的密钥，每次都有记录；场景 2（甲用下发的模型密钥调用模型）、场景 13（轮换后 `secrets reload` 就生效，旧密钥不再使用）、场景 6（停用后取不到） |
| S6-01 | 加密密钥可轮换 | 通过 | `enc:v1:<key_id>:` 引用 + `rotate_credentials`；待审批记录在轮换期间仍能执行（`test_oc6_security.py`）；部署文档第 6.1 节 |
| S6-02 | 管理员端 IM（后端中转） | 通过 | 单聊、拉群、会话与消息置顶、免打扰、分组（场景 2 ~ 4）；不能删好友（场景 5，网关拦截 + 回调后补回）；上线自动加好友（场景 1）；浏览器不直连 OpenIM |
| S6-02 | MCP 只读工具与权限边界 | 通过 | 工具按实例 `scope` 列出、按“登记人角色与 `max_role` 中较低的一个”检查；接入网关只放行白名单路由并校验参数（场景 8）；跨工作空间隔离测试 |
| S6-03 | 写工具审批（参数哈希、过期、二次认证） | 通过 | 见 DoD 第一条与 A14 |
| S6-03 | 群聊防刷屏 | 通过 | 实例连续 8 条后暂停并通知管理员、管理员发言后恢复、每分钟条数上限（场景 3） |
| S6-03 | 审计 | 通过 | 工具调用记录（含读工具）、审批决定、取密钥记录；审批卡片标明来源 |
| S6-03 | 反 Prompt Injection 与越权测试 | 通过 | `test_oc6_security.py`（14 项）、`test_im_gateway.py`、`test_security_matrix.py`（自动覆盖 V2 接口）；场景 10、11 |
| 方案第 2、6.1、7 节 | 运维页、监控、镜像扫描 | 通过 | T-0062（验收中发现漏做后补上）：运维页“OpenClaw 与 IM”一组；`/metrics` 的 V2 指标与 Grafana 面板；第三方镜像扫描覆盖 OpenIM 依赖、OpenClaw、ghostunnel |

**多实例端到端（`tests/e2e/agents_acceptance.py`，方案第 8 节）**

两个真实 OpenClaw 实例：甲走内网直连（read_write），乙走外网经本机隧道（read，双向证书）；工作台加打过补丁的 OpenIM；模型用假模型（OpenAI 兼容，按测试指令回复，记下每次请求用的密钥）。main 上 CI 运行 41313979474 的 agents 任务结果（PR #104 的运行 41310484116 结果相同）：

| 场景 | 检查 | 结果 |
|---|---|---|
| 1 | 登记后两个实例自动出现在通讯录；上线后显示“在线”，已与管理员互为好友 | 通过 |
| 2 | 管理员私聊甲，甲经假模型回复；甲用工作台下发的模型密钥调用模型；取密钥记录 | 通过 |
| 3 | 群里只有被 @ 的回复；连续 8 条后自动暂停，之后的实例消息被拒收；管理员收到通知；管理员发言后恢复 | 通过 |
| 4 | 置顶会话排在最前；置顶消息显示在会话顶部；免打扰的会话不计入未读总数；按分组筛选 | 通过 |
| 5 | 经接入网关删好友被拦截；绕过网关删除后自动补回并告警管理员 | 通过 |
| 6 | 停用乙：取密钥失败、MCP 返回 401、IM 被强制下线 | 通过 |
| 7 | 外网实例的凭证用在内网入口、内网实例的凭证经外网入口，都被拒绝 | 通过 |
| 8 | 改会话、搜消息、管理接口、拉别人的消息都被接入网关拦截 | 通过 |
| 9 | 高危删除只生成待审批；IM 出现审批卡片；二次认证批准后才执行；改参数要重新审批（A14） | 通过 |
| 10 | 群里诱导甲去让乙删除数据：乙没有响应，没有新的执行 | 通过 |
| 11 | 甲私聊乙被拒收，乙收不到 | 通过 |
| 12 | 丢弃的回调由周期对账补回；重启 OpenIM 与 API 后实例自动重连，消息照常往返 | 通过 |
| 13 | 轮换模型密钥：`secrets reload` 之后下一次模型请求就用新密钥 | 通过 |
| — | 管理员的 IM 账号就绪 | 通过 |

**验收中发现并修复的产品问题**

- 接入包的 `openclaw.json` 带 `_comment` 键，锁定版本拒绝未知键，实例起不来；
- 配置引用了还没装的 `openim` 渠道时 `plugins install` 被拒，检查清单改为用临时空配置安装插件；
- 接入网关拦了 SDK 要用的三个只读查询（好友申请、好友资料、群成员），插件里的拒绝没人处理，OpenClaw 退出或发消息失败；改为放行并按工作空间 / 群成员校验参数；
- 方案要求的运维页状态、Prometheus 指标与镜像扫描覆盖漏做（T-0062）。

## 4. Sprint 1 ~ 5 回归（CI 运行 41185591245）

| 验收 | 结果 |
|---|---|
| Sprint 1 接口级（A01 / A02 / A18 / 跨工作空间） | 全部通过 |
| Sprint 2 接口级（A03 / A04 / A05 / DoD） | 全部通过 |
| Sprint 3 接口级（DoD / A05 ~ A09，真实 Vikunja 2.7.1） | 全部通过 |
| Sprint 4 接口级（DoD / A10 ~ A13 / A17） | 全部通过 |
| Sprint 5 接口级（DoD / S5-01 ~ S5-05 / A16） | 全部通过 |
| Sprint 1 ~ 5 浏览器验收 | 全部通过（页面无错误） |
| 停止后重启数据卷保留；清空数据卷后重新迁移 | 通过 |

## 5. 交付完成定义（规范第 18 节，针对 Sprint 6）

| 条件 | 结果 | 说明 |
|---|---|---|
| 代码已提交到版本控制 | 通过 | PR #63 ~ #66 与 V2 的 #72 ~ #105 都已合并到 `main` |
| 所有必需依赖固定版本 | 通过 | 所有镜像 tag + digest；`uv.lock` 与各 `package-lock.json` 锁定；工具镜像也锁定 digest |
| 开发和部署命令可复现 | 通过 | `make up / migrate / smoke / smoke-full / backup / restore-verify / scan / perf`，本机与 CI 共用同一套脚本 |
| 迁移可执行 | 通过 | `0001` ~ `0017`；CI 跑 `upgrade head → downgrade base → upgrade head`；恢复演练在恢复出来的数据上 `upgrade head` |
| 自动测试通过 | 通过 | 后端 pytest 398 项通过（另有 7 项需要真实外部服务的测试跳过）；前端 vitest 269 项；全栈与浏览器验收 |
| 手工 Smoke Test 通过 | 通过 | 本机 `make smoke-full` 多次通过；锁定 digest 后重建容器再跑也通过 |
| 关键安全测试完成 | 通过 | 接口安全矩阵、SSRF / DNS 重绑定、依赖 / 密钥 / 镜像扫描；V2 的反 Prompt Injection、越权、绕过审批、接入网关与 OpenIM 拦截、回调伪造测试（`test_oc6_security.py` 等）与多实例端到端 |
| README / 配置示例更新 | 通过 | README、部署文档已更新；新的可选配置（连接池、首页聚合并发）写在部署文档第 3 节，默认值即可用 |
| 未实现能力明确标注 | 通过 | V2 只用假模型验证、两种网络模式在同一台机器上模拟，见第 6 节；README、部署文档与本报告都写明了 |
| 无明文密钥 | 通过 | gitleaks 扫描整个历史无发现；`.env` 不入库 |
| 无未经验证的第三方 API 假设 | 通过 | restic、pg_dump、gitleaks、trivy、pip-audit、zod / vee-validate、各镜像许可证都有核验 ADR |

## 6. 未运行 / 限制

1. **V2 只用假模型验证**：工作台的边界（密钥下发、审批、网关、防刷屏、对账）与模型无关；接真实模型服务商要人类在密钥库录入模型密钥（部署文档第 18.3 节）。
   **两种网络模式在 CI 的同一台机器上模拟**：OpenClaw 服务器用占位容器代替，内网 / 外网入口用自签名测试证书。真实的分开部署要人类准备服务器、域名与私网（第 18 节）。
   **已知的上游行为**（不影响权限边界，部署文档第 18.5 节）：OpenIM 重启后 openclaw-channel 的 SDK 抛出未处理的错误，OpenClaw 自动重启（约 45 秒），这段时间的消息插件不补处理；实例间私聊被拒收时 `message` 工具仍显示 done。
   **遗留问题**（不影响验收，建议另建任务）：`agent_secret_accesses` 与 `ai_tool_executions` 没有清理任务，会一直增长；过期的未用图片上传只删记录、不删 Garage 里的对象；方案写 IM 消息默认保留 180 天，实现是 365 天（`IM_MESSAGE_RETENTION_DAYS`）。
2. **异地备份后端没有实测**：本环境没有对象存储与凭据，只测了本地目录仓库。接入真实存储需要人类在环境的 Secret 里提供凭据（核验 ADR 第 7 节）。
3. **并发 16 时首页聚合的 P95 在本机超标**（565 ms，目标 500 ms），原因是单进程吞吐上限。改进需要多进程运行 api，要先把进程内状态移到 Redis（ADR 第 8.6 节）。
4. **第三方镜像有已修复版本的漏洞**，要等上游发布新版本，发布前按 checklist 跟进。
5. **Redis 7.4 的许可证**是 RSALv2 / SSPLv1，自托管自用可以。对外提供服务前需要人类决定是否更换（`docs/decisions/2026-10-31-release-versions-and-licenses.md` 第 3 节）。
6. **Grafana 不在 CI 里启动**（与 Sprint 5 相同）。
7. **`ci-web.yml` 用浮动的 Node 22 小版本**，web 镜像是 22.23.4。已列入发布 checklist。

## 7. 下一步

- Sprint 6（S6-01 ~ S6-06）全部交付，规范第 18 节的条件按上表满足。需要人类决定：
  1. 是否进入正式部署：准备服务器 A 的资源与接入域名、私网、OpenClaw 服务器，录入模型密钥（部署文档第 18 节）；
  2. 第 6 节的 V2 遗留问题是否建任务处理（清理任务、Garage 对象、IM 消息保留期按方案改回 180 天还是保持 365 天）；
  3. Redis 许可证；
  4. 是否提供异地备份的存储与凭据。
