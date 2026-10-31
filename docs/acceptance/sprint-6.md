# Sprint 6 验收报告（S6-04 ~ S6-06）

- 验收人：claude（协作任务 T-0046；按 D-0003 负责每个 Sprint 的联调与验收；codex 暂停期间按 D-0007 独立完成所有任务）
- 日期：2026-10-31
- **结论**：
  - **S6-04 ~ S6-06 全部通过**。DoD 第三条“能从备份恢复并通过 Smoke Tests”与 A15 已通过，并在 CI 里每次自动运行。Sprint 1 ~ 5 的验收继续全部通过。
  - **Sprint 6 没有全部完成**：S6-01 ~ S6-03（AI）、DoD 的前两条和 A14 还没有开始。2026-10-31 人类在对话中暂停了 AI 服务的选型，这部分由 T-0047 等人类恢复后再做（第 6 节）。
  - 因此 `docs/TASKS.md` 只勾选 S6-04 ~ S6-06。
- 本 Sprint 合并的 PR：
  - [#63](https://github.com/brianabc121/WorkspaceCore/pull/63)：T-0041 核验、T-0042 备份恢复；
  - [#64](https://github.com/brianabc121/WorkspaceCore/pull/64)：T-0043 安全加固；
  - [#65](https://github.com/brianabc121/WorkspaceCore/pull/65)：T-0044 扫描、基准与完整冒烟，以及修复首页聚合在并发下超时；
  - [#66](https://github.com/brianabc121/WorkspaceCore/pull/66)：T-0045 发布文档与 digest 锁定、T-0048 修复首页刷新闪烁。

**证据**：#66 合并前最新提交（`142a196`）上的 CI，7 项检查全部成功。

| 工作流 | 运行 | 内容 |
|---|---|---|
| `ci-infra` | [41185591245](https://github.com/brianabc121/WorkspaceCore/actions/runs/41185591245) | 全栈：Sprint 1 ~ 5 接口级与浏览器验收、完整冒烟、性能基准、A15、重启与数据卷检查 |
| `ci-security` | [41185591256](https://github.com/brianabc121/WorkspaceCore/actions/runs/41185591256) | 依赖、密钥泄露、容器镜像扫描 |
| `ci-api` / `Web CI` / `collab` | 同一提交 | 后端 ruff / mypy / pytest / 迁移冒烟；前端 lint / typecheck / test / build / check:api；协作检查 |

本机数据来自 claude 自己的 Docker 环境（D-0006）：共享的云端容器，单个 uvicorn 进程。

## 1. Sprint 6 DoD（`docs/DEVELOPMENT_SPEC.md` 第 13 节）

| 条件 | 结果 | 证据 / 原因 |
|---|---|---|
| 无审批不能执行高危操作 | **未运行** | AI 工具与审批（S6-01 ~ S6-03）还没有开始（第 6 节）。现有的自动化（n8n）已按策略校验：白名单、参数校验、角色、`Idempotency-Key`、结果未知时不重发，见 Sprint 5 验收报告 |
| 攻击性文档内容无法绕过工具策略 | **未运行** | 同上；目前没有 AI 入口 |
| 能从备份恢复并通过 Smoke Tests | **通过** | A15（第 2 节）：`make backup` → 在隔离的空环境 `make restore-verify` → 迁移检查、冒烟检查、数据检查 → 原账号登录、显示名、首页布局与便签、日程、审计都与备份时一致；正在运行的栈不受影响。CI 每次运行 |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A14 AI 试图执行未经审批的删除 | Tool Executor 拒绝并记录 | **未运行**：AI 未实现（第 6 节） |
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

## 5. 交付完成定义（规范第 18 节，针对 S6-04 ~ S6-06）

| 条件 | 结果 | 说明 |
|---|---|---|
| 代码已提交到版本控制 | 通过 | PR #63 ~ #66 都已合并到 `main` |
| 所有必需依赖固定版本 | 通过 | 所有镜像 tag + digest；`uv.lock` 与各 `package-lock.json` 锁定；工具镜像也锁定 digest |
| 开发和部署命令可复现 | 通过 | `make up / migrate / smoke / smoke-full / backup / restore-verify / scan / perf`，本机与 CI 共用同一套脚本 |
| 迁移可执行 | 通过 | `0001` ~ `0010`；CI 跑 `upgrade head → downgrade base → upgrade head`；恢复演练在恢复出来的数据上 `upgrade head` |
| 自动测试通过 | 通过 | 后端 pytest 398 项通过（另有 7 项需要真实外部服务的测试跳过）；前端 vitest 269 项；全栈与浏览器验收 |
| 手工 Smoke Test 通过 | 通过 | 本机 `make smoke-full` 多次通过；锁定 digest 后重建容器再跑也通过 |
| 关键安全测试完成 | 通过（不含 AI） | 接口安全矩阵、SSRF / DNS 重绑定、依赖 / 密钥 / 镜像扫描。反 Prompt Injection 测试属于 S6-03，未运行 |
| README / 配置示例更新 | 通过 | README、部署文档已更新；新的可选配置（连接池、首页聚合并发）写在部署文档第 3 节，默认值即可用 |
| 未实现能力明确标注 | 通过 | AI 助手页是占位说明；README、部署文档与本报告都写明了 |
| 无明文密钥 | 通过 | gitleaks 扫描整个历史无发现；`.env` 不入库 |
| 无未经验证的第三方 API 假设 | 通过 | restic、pg_dump、gitleaks、trivy、pip-audit、zod / vee-validate、各镜像许可证都有核验 ADR |

## 6. 未运行 / 限制

1. **AI（S6-01 ~ S6-03）、DoD 前两条、A14 未开始**：2026-10-31 人类在对话中暂停了 AI 服务的选型（记录在 T-0040、T-0047）。T-0047 的 owner 是 human，等人类恢复选型后拆分并实施。
2. **异地备份后端没有实测**：本环境没有对象存储与凭据，只测了本地目录仓库。接入真实存储需要人类在环境的 Secret 里提供凭据（核验 ADR 第 7 节）。
3. **并发 16 时首页聚合的 P95 在本机超标**（565 ms，目标 500 ms），原因是单进程吞吐上限。改进需要多进程运行 api，要先把进程内状态移到 Redis（ADR 第 8.6 节）。
4. **第三方镜像有已修复版本的漏洞**，要等上游发布新版本，发布前按 checklist 跟进。
5. **Redis 7.4 的许可证**是 RSALv2 / SSPLv1，自托管自用可以。对外提供服务前需要人类决定是否更换（`docs/decisions/2026-10-31-release-versions-and-licenses.md` 第 3 节）。
6. **Grafana 不在 CI 里启动**（与 Sprint 5 相同）。
7. **`ci-web.yml` 用浮动的 Node 22 小版本**，web 镜像是 22.23.4。已列入发布 checklist。

## 7. 下一步

- 需要人类决定：
  1. 恢复 AI 服务选型，以开始 S6-01 ~ S6-03；
  2. Redis 许可证；
  3. 是否提供异地备份的存储与凭据。
- 在此之前，S6-04 ~ S6-06 已交付；规范第 18 节其余条件按上表满足。
