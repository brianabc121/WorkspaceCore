# Sprint 6 核验：备份与恢复、加密与保留、安全扫描与性能基准（T-0041）

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0041；codex 暂停期间按 D-0007 独立完成）
- 用途：T-0042（备份恢复，S6-04）与 T-0044（扫描、基准与 Smoke Test，S6-05）的依据。项目规则禁止猜测第三方工具的版本与行为。

## 1. 方法

- 环境：claude 自己的 Docker（D-0006），Compose 全栈正在运行（迁移到 0010，含 Sprint 1 ~ 5 验收留下的数据）。
- 来源：
  - 各项目在对应 tag 下的源码与文档：restic `v0.19.1` 的 `doc/*.rst`，PostgreSQL `REL_16_15` 的 `doc/src/sgml/ref/pg_dump.sgml`，trivy `v0.76.1` 的 `LICENSE`。
  - Docker Hub 的 tag 与 digest、PyPI、npm registry 的元数据。
  - 运行实例实测（第 2 ~ 5 节的命令都实际跑过）。
- 文档网站（restic.readthedocs.io、postgresql.org、google.github.io）被本环境的网络策略拒绝，GitHub 的 Release 页面与 API 也不可用，所以改读各项目在 tag 下的源码。
- 版本选择：每个工具都选写这份 ADR 时的最新稳定版，按 tag 加 digest 锁定（生产禁止浮动 `latest`）。

## 2. PostgreSQL 逻辑备份

| 项目 | 结论 | 来源 |
|---|---|---|
| 工具版本 | 用 `postgres:16.15-alpine3.24` 镜像自带的 `pg_dump` / `pg_restore`，与服务端同为 16.15（`pg_dump --version`、`select version()` 实测） | 运行实例 |
| 一致性 | “It makes consistent backups even if the database is being used concurrently. pg_dump does not block other users accessing the database (readers or writers).”——备份时不用停服务 | `pg_dump.sgml`（REL_16_15）第 40 行 |
| 格式 | custom 格式（`-Fc`，默认压缩），用 `pg_restore` 恢复，可以用 `pg_restore --list` 检查归档完整 | `pg_dump.sgml` 第 320 行起 |
| 扩展 | `pgcrypto` 1.3、`pg_trgm` 1.6 装在 `workspace` schema 里，随备份一起恢复 | 运行实例 |
| 迁移版本表 | `public.alembic_version`（不在 `workspace` schema 里），随备份一起恢复 | 运行实例 |

实测（数据库约 50 个用户、230 个组件、255 条审计）：

1. `docker compose exec -T postgres pg_dump -U <用户> -d <库> -Fc -Z 6 > ws.dump`：1.3 秒，148 KB；`pg_restore --list` 列出 181 项。
2. 起一个空的 `postgres:16.15-alpine3.24` 容器，`POSTGRES_USER` 与原库相同：`pg_restore -U <用户> -d <库> --exit-on-error < ws.dump`，退出码 0，0.5 秒。
3. 恢复前后的用户、仪表盘、组件、审计行数完全一致，迁移版本都是 `0010`，两个扩展都在 `workspace` schema 里。

结论：
- 恢复目标库要用与原库相同的 `POSTGRES_USER`（Compose 默认即如此），它是超级用户，能创建扩展、保留属主。
- 用户名不同时，加 `--no-owner --role=<用户>`。

## 3. 加密、异地与保留：restic 0.19.1

### 3.1 选型

| 方案 | 加密 | 异地 | 保留策略 | 结论 |
|---|---|---|---|---|
| **restic** | 仓库自带 | 本地目录、SFTP、REST Server、S3 / S3 兼容、rclone 等 | `forget` 按时间段保留 | **选用**：三项需求一个工具满足，恢复与校验命令完整 |
| age 加密 + 自己复制与清理 | 有 | 要自己写上传 | 要自己写清理 | 不选：要自己实现的部分多，也更容易出错 |

### 3.2 版本与许可证

- 镜像：`restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510`，Docker Hub 上 2026-07-05 更新，约 66 MB。
  - `restic version` 输出 `restic 0.19.1 compiled with go1.26.4 on linux/amd64`。
- 许可证：BSD 2-Clause（源码 `LICENSE`）。
- 源码 tag：`v0.19.1`（`6aa3a516ce654808a1f28f9fa21e9b7c8e6e90bf`）。

### 3.3 行为（源码文档与实测）

- **加密**：仓库里所有数据用 AES-256-CTR 加密，用 Poly1305-AES 校验完整性（`doc/design.rst` 第 51、314 行）。
  - 实测：在仓库目录里搜索 pg_dump 归档的文件头 `PGDMP`，0 个文件命中。
- **密码**：从 `RESTIC_PASSWORD_FILE`（或 `--password-file`）读取，仓库地址用 `RESTIC_REPOSITORY`（`doc/030_preparing_a_new_repo.rst` 第 31 ~ 45 行）。
  - **丢失密码就无法恢复数据**（`init` 的提示原文）。
- **后端**：Local、SFTP、REST Server、Amazon S3、S3 兼容存储、rclone 等（同一文件第 67 行起）。测试与 CI 用本地目录；生产在 `.env` 里配置异地仓库。
- **备份与恢复**（实测）：`init` → `backup --tag workspace` → `check`（`no errors were found`）→ `restore latest`。恢复出的文件与原文件 SHA-256 相同。
- **从 stdin 备份**：`--stdin-from-command` 会检查命令的退出码；普通的 `--stdin` 判断不了数据是否完整（`doc/040_backup.rst` 第 684 ~ 720 行）。
  - restic 镜像里没有 `pg_dump`，所以 T-0042 先在权限 0700 的临时目录里生成归档，用 `pg_restore --list` 确认完整后再交给 restic，最后删除临时文件。
- **保留策略**：`forget --keep-within-daily 7d --keep-within-weekly 30d --keep-within-monthly 90d --prune`（`doc/060_forget.rst` 第 191 ~ 206、282 行；注意 `1w` 不是合法的时间长度）。
  - 实测：用 `backup --time` 造了 27 个快照，分布在 0 ~ 130 天前。`forget --dry-run` 的结果：
    - 保留：0 ~ 6 天前每天一份；13、20、27 天前每周一份；31、66 天前（9 月、8 月的最后一份）每月一份。
    - 删除：其余 15 份，包括 90 天以前的全部。
  - 这就是规范 11.4 的“保留 7 / 30 / 90 日”。三个时间段都做成可配置项，默认值就是这三个。

### 3.4 镜像本身的漏洞（第 5.4 节的扫描）

- trivy 扫描这个镜像：35 个已有修复版本的高危 / 严重漏洞，来自 Alpine 包（jq、openssl、openssh-client）和 Go 依赖。
- 这个容器只在备份时临时运行，不监听端口；用本地或 S3 仓库时也用不到 jq 和 openssh。
- 处理：记为已知风险并跟进上游的新版本，见第 5.4 节的策略。

## 4. 备份范围（T-0042 照此实现）

| 数据 | 是否进 restic 仓库 | 理由 |
|---|---|---|
| 工作台 PostgreSQL（`workspace` 库，含 `public.alembic_version`） | 是 | 所有业务数据 |
| `.env`（`WORKSPACE_SECRET_KEY`、`CREDENTIAL_ENCRYPTION_KEY`、数据库密码、restic 密码本身等） | **否**，单独保存 | 部署文档已要求“与数据库备份分开保存”：同一个仓库里同时放密文和解密钥匙，等于没有加密。恢复演练用新生成的 `.env`；真实恢复用单独保管的那份 |
| Caddy 数据卷（证书） | 否 | 证书可以重新签发；本地部署用的是自签名或 HTTP |
| Redis | 否 | 只有缓存、任务队列、SSE 事件流、限流计数与心跳。丢失后由周期对账与重新拉取恢复；会话、幂等记录都在 PostgreSQL 里 |
| Vikunja、n8n、AFFiNE、Grafana / Prometheus | 否（各自备份） | 独立部署的组件。Vikunja 用 `vikunja dump`；n8n 备份它的数据卷和 `N8N_ENCRYPTION_KEY`；AFFiNE 的 PostgreSQL、storage、config 要一起备份（规范 11.4）；Prometheus 只保留 7 天，不备份 |

## 5. 安全扫描

### 5.1 Python 依赖：pip-audit 2.10.1

- 版本与许可证：PyPI 上 2.10.1（2026-06-10），Apache-2.0，源码 `https://github.com/pypa/pip-audit`。
- 用法：`uvx --from pip-audit==2.10.1 pip-audit -r <(uv export --frozen --no-dev --no-emit-project --format requirements-txt) --require-hashes --disable-pip`。
  - 用 `uv.lock` 导出的锁定版本加哈希来审计，不在本机安装任何东西。
- 实测（services/api，401 行锁定依赖）：`No known vulnerabilities found`。
- 漏洞数据来自 PyPI 的 JSON API（本环境可以访问）。

### 5.2 npm 依赖：`npm audit`

- 版本：npm 自带，不需要另装。CI 用 Node 22 自带的 npm，本机是 npm 10.9.4。
- 用法：`npm audit --omit=dev --audit-level=high`（运行时依赖），另外跑一次包含开发依赖的完整审计。
- 实测：
  - `tests/e2e`：`found 0 vulnerabilities`。
  - `apps/web`：**2 个高危，都来自 zod**。
    - 公告是 GHSA-mrhx-c7xc-m3w5“Zod can exhaust memory while validating oversized constrained collections”，影响范围 `<4.6.6`；
    - 装的是 `zod@3.25.76`；`@vee-validate/zod@4.15.1`（npm 上的最新版）的 peer 依赖是 `zod ^3.24.0`，所以 `npm audit` 报“没有可用修复”。
    - 影响：校验发生在用户自己的浏览器里，最坏是那个标签页卡死，服务端不受影响。
    - 处理：**T-0044 处理**——把 zod 升级到 4.6.6 或更高版本，去掉 `@vee-validate/zod`，改用 vee-validate 对 Standard Schema 的支持或一个小的本地适配；升级前先核验它们的接口。

### 5.3 密钥泄露：gitleaks v8.30.1

- 镜像：`zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`；许可证 MIT（镜像标签 `org.opencontainers.image.licenses`），源码 `https://github.com/gitleaks/gitleaks`。
- 用法：`gitleaks git /repo --no-banner --redact`，扫描整个 git 历史。CI 的 checkout 要用 `fetch-depth: 0`，否则只扫到最近的提交。
- 实测（本机的浅克隆，88 个提交，4.6 MB，3.8 秒）：5 处，全部是有意写进仓库的测试数据：
  - 4 处 `generic-api-key`：`services/api/tests/` 里的假密钥（`fake_n8n.py`、`test_automations_api.py`、`test_contract.py`、`test_vikunja_connector.py`）；
  - 1 处 `jwt`：Vikunja 官方 OpenAPI 副本（`infra/vikunja/openapi/vikunja-2.7.1-api-v2.json`）里的示例 token。
- 处理：T-0044 加 `.gitleaks.toml`，按路径列入允许列表并写明理由，其余发现都让 CI 失败。

### 5.4 容器镜像：trivy 0.76.1

- 镜像：`aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`（Docker Hub 上 2026-10-21 更新）；许可证 Apache-2.0（源码 `v0.76.1` 的 `LICENSE`）。
- 漏洞库：运行时从 `mirror.gcr.io/aquasec/trivy-db` 下载。
  - 本机的出网代理在宿主机的回环地址上，trivy 容器要用 `--network host` 才能连到；GitHub Actions 的 runner 可以直接下载。
  - 缓存目录约 1.6 GB，CI 要用缓存或预留空间。
- 用法：`trivy image --severity HIGH,CRITICAL --ignore-unfixed <镜像>`（只看已有修复版本的漏洞）。
- 实测（2026-10-31 的漏洞库）：

  | 镜像 | 已有修复版本的高危 / 严重漏洞 | 主要来源 |
  |---|---|---|
  | `my-workspace-api:local`（本仓库构建，Debian 13.7） | 0 | — |
  | `my-workspace-web:local`（本仓库构建，nginx-unprivileged，Alpine 3.24.2） | 3 | `brotli-libs`（严重）、`curl` / `libcurl` |
  | `postgres:16.15-alpine3.24` | 26 | 镜像里的 `gosu` 用较旧的 Go 编译（Go 标准库的漏洞） |
  | `redis:7.4.12-alpine3.21` | 0 | — |
  | `caddy:2.11.7-alpine` | 9 | Alpine 包、`go-jose`、`x/net`、Go 标准库 |
  | `restic/restic:0.19.1` | 35 | Alpine 包、Go 依赖 |

- **策略（T-0044 实现）**：
  - **本仓库构建的镜像**（api、web）：出现已有修复的高危或严重漏洞，CI 失败。
    - web 镜像的 3 个，由 T-0044 处理：在构建里升级 Alpine 包，或换用包含修复的基础镜像 digest。
  - **第三方锁定镜像**（postgres、redis、caddy、restic，以及 prometheus、grafana、n8n、vikunja）：只报告，不让 CI 失败。
    - 把清单写进验收报告，发布前按 checklist（S6-06）跟进上游的修复版本，并更新锁定的 digest。
    - 理由：这些镜像的修复只能来自上游的新版本；每升级一次都要按项目规则重新核验，不能为了让扫描变绿就自动跟随最新版本。

## 6. 性能基准

- 不引入新工具：用 Python 标准库（`http.client` + 线程）写基准脚本，放在 `tests/perf/`。它和 `tests/e2e/` 一样只依赖标准库，本机与 CI 共用。
- 测法（规范第 12 节的两个目标是测试目标，不是承诺）：
  - **基础 API P95 < 300 ms**：已登录用户的只读接口，例如 `GET /api/v1/me`、`/api/v1/tasks`、`/api/v1/calendar/events`、`/api/v1/notifications`。经 Caddy 访问，不含外部服务。
  - **缓存命中的首页 P95 < 500 ms**：`GET /api/v1/dashboard/overview`，先预热一次让缓存命中。
  - 每个接口先预热，再测固定的请求次数（例如 200 次，并发 4 和 16 各一组）。报告 P50 / P95 / P99 与错误数，结果写成 JSON 与表格。
  - CI 的 runner 性能不稳定：CI 里只要求“没有错误、P95 在目标的 2 倍以内”，并记录数值；正式的对比数据以本机的固定环境为准，写进验收报告。

## 7. 未核验 / 留给后续

- **restic 的异地后端**（S3、SFTP 等）没有实测，只测了本地目录仓库：本环境没有对象存储，也没有凭据。T-0042 把后端做成配置项，部署文档写清楚怎么配置；接入真实存储时需要人类提供（放在环境的 Secret 里）。
- **zod 4 + vee-validate 的接法**：留给 T-0044，升级前先读两者在锁定版本下的源码。
- **trivy 漏洞库下载**：在 GitHub Actions 上的耗时与稳定性，在 T-0044 的 CI 里确认。

## 8. T-0044 落实结果（2026-10-31）

本节记录第 5、6 节的计划落实时的核验与偏差。命令都在 claude 自己的 Docker 环境里实际跑过（D-0006）。

### 8.1 zod 4 与表单校验（第 5.2 节）

- 版本：`zod@4.6.7`（npm 上 2026-10-12 发布，高于公告的修复版本 4.6.6）。
- 去掉 `@vee-validate/zod`：它的最新版 4.15.1 只支持 zod 3（peer 依赖 `zod ^3.24.0`）。
- 改用本地适配 `apps/web/src/services/forms.ts`，按 vee-validate 4.15.1 的 `TypedSchema` 接口实现：
  - 接口见 `node_modules/vee-validate/dist/vee-validate.d.ts` 第 180 ~ 199 行：
    `__type: 'VVTypedSchema'`、`parse(values) → Promise<{ value?, errors: { path?, errors: string[] }[] }>`、可选的 `cast`、`describe`；
  - 适配实现了 `parse` 与 `cast`；`describe`（用来推断字段是否必填）现有表单都没有用到，没有实现。
- 升级时遇到的 zod 4 行为差异：
  - 对象的 `.default({})` 不再套用内部字段的默认值，组件配置改用 `.prefault({})`；
  - `required_error` 改为 `error`；
  - `invalid_union` 的错误放在 `issue.errors` 里，适配会把它们展开到各自的字段。
- 结果：`npm audit --omit=dev` 为 0；lint、typecheck、267 项测试、build、check:api 全部通过。

### 8.2 开发依赖：vitest 4.1.12

- 原来的 `vitest@3.2.7` 带来 2 个严重漏洞与 2 个中危漏洞：
  - `tinypool`：GHSA-5gmw-xhrv-c9v3（≤ 2.1.0）、GHSA-85c8-ppgw-ccpr（< 2.1.2），严重；
  - `@vitest/mocker`：GHSA-82fw-gwwq-j7x9（< 4.1.11）、GHSA-qxg6-89mg-rqv8（< 4.1.12），中危。
- 升级到 `vitest@4.1.12`（npm 上 2026-10-15 发布）：
  - 它支持 `vite ^6 || ^7 || ^8`（现用 vite 7.3.7），不再依赖 tinypool；
  - 测试代码不用改，267 项测试全部通过。
- 锁文件用 npm 11.6.2 生成：npm 10.9.4 在解析 peer 依赖时报错（`Cannot read properties of null (reading 'edgesOut')`，
  出自 `@npmcli/arborist` 的 `#loadPeerSet`）。用 npm 10.9.4 对这份锁文件执行 `npm ci` 正常，CI 用 Node 22 自带的 npm。
- 剩下 19 个中危，都在开发工具里：
  - `minimatch < 11.0.1`（GHSA-g3hv-75wg-m249），来自 eslint 9、typescript-eslint、openapi-typescript 与 `@vue/test-utils` → js-beautify；
  - `glob`（GHSA-3g3m-2fjr-6pw4），同样来自 js-beautify。
  - 没有不破坏兼容的修复：`npm audit fix` 会装 eslint 4。这些工具只在开发机与 CI 上处理仓库自己的文件，按策略只报告。

### 8.3 前端镜像（第 5.4 节）

- 原计划是构建时 `apk upgrade`。但本环境的网络策略拒绝 Alpine 的软件源（`dl-cdn.alpinelinux.org` 返回 403），无法在本地验证。
- 改用基础镜像的 slim 变体：`nginxinc/nginx-unprivileged:1.30.6-alpine3.24-slim`，
  digest `sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6`（Docker Hub 上 2026-10-19 更新）。
  - 包含的包：nginx、Alpine 基础包、busybox（健康检查用它的 `wget`）、`gettext-envsubst`、OpenSSL 3.5.9 等；
    没有 curl、freetype、brotli。
  - 默认用户仍是 nginx（101），入口脚本相同。
- 结果：
  - trivy 扫描 0 个已有修复的高危 / 严重漏洞（完整变体是 3 个）；
  - 镜像从 90 MB 降到 30 MB；
  - `make smoke` 与完整冒烟都通过。
- 构建不访问 Alpine 的软件源，同一个 digest 构建出来的结果相同。

### 8.4 密钥扫描：用指纹而不是路径登记误报（第 5.3 节）

- 原计划是 `.gitleaks.toml` 按路径列入允许列表。改为 `.gitleaksignore` 按指纹登记（`<提交>:<文件>:<规则>:<行号>`），范围更窄：
  - 只忽略已登记的那 5 处，同一个文件里以后再出现的密钥仍然会让扫描失败；
  - 实测：在 `services/api/tests/fake_n8n.py` 再加一个假密钥并提交，gitleaks 退出码 1。
- gitleaks 8.30.1 通过 `--gitleaks-ignore-path` 读取这个文件，以 `#` 开头的说明行会被忽略（实测）。
- 用默认规则，不需要 `.gitleaks.toml`。

### 8.5 镜像扫描结果（2026-10-31 的漏洞库）

`scripts/security-scan.sh images` 的实测结果，只统计已有修复版本的高危 / 严重漏洞：

| 镜像 | 严重 | 高危 | 处理 |
|---|---|---|---|
| `my-workspace-api:local` | 0 | 0 | 有就让 CI 失败 |
| `my-workspace-web:local` | 0 | 0 | 有就让 CI 失败 |
| `caddy:2.11.7-alpine` | 1 | 8 | 只报告。这个 tag 在 2026-10-30 重新构建过，但漏洞没变（brotli-libs、curl，Go 标准库、go-jose、x/net） |
| `grafana/grafana:13.2.5` | 0 | 35 | 只报告（内置插件的 Go 依赖） |
| `n8nio/n8n:2.45.7` | 1 | 32 | 只报告（brotli-libs 与 Node 依赖） |
| `postgres:16.15-alpine3.24` | 1 | 25 | 只报告（`gosu` 用较旧的 Go 编译） |
| `prom/prometheus:v3.15.1` | 0 | 0 | — |
| `redis:7.4.12-alpine3.21` | 0 | 0 | — |
| `restic/restic:0.19.1` | 3 | 32 | 只报告（openssh、jq、Go 依赖；只在备份时临时运行） |
| `vikunja/vikunja:2.7.1` | 2 | 7 | 只报告（echo 框架） |

Caddy 是对外的入口，发布前要优先查看上游有没有包含修复的新版本（S6-06 的发布 checklist）。

### 8.6 性能基准：发现的问题与修复（第 6 节）

- 落实：`tests/perf/benchmark.py` 写入 200 条任务投影（带同步游标，任务模块会实际查询）、40 个本周日程、50 条通知
  （`tests/e2e/seed.py bulk` 与日历接口），其余按第 6 节的测法。
- **第一次测量发现一个缺陷**：
  - 现象：并发 16 时，首页聚合的 P95 是 2.2 秒；64 次请求里有 28 次的全部模块都是 `unavailable` / `timeout`。
  - 原因：首页每次聚合有 5 个模块，各开一个数据库会话并发查询；请求自己的会话也一直占着连接。
    默认连接池只有 5 + 10 个连接，并发一多，模块都在等连接，等的时间算进了模块 2 秒的超时。
- **修复**（`services/api`）：
  1. 聚合前先结束请求自己的只读事务，把连接还给连接池（sessionmaker 是 `expire_on_commit=False`，已读出的数据不受影响）；
  2. 同一个进程里同时进行的聚合数用信号量限制（`DASHBOARD_OVERVIEW_CONCURRENCY`，默认 3）。超出的请求排队，排队的时间不计入模块超时；
  3. 连接池大小可配置（`DATABASE_POOL_SIZE` 默认 10、`DATABASE_MAX_OVERFLOW` 默认 10）。
  - 测试：`tests/test_dashboard_api.py::test_concurrent_overviews_queue_instead_of_timing_out`。去掉修复时这个测试失败。
- **修复后**：并发 16 的 64 次请求全部模块正常。本机（共享的云端容器，单个 uvicorn 进程）的 P95 如下，单位 ms：

  | 接口 | 并发 4 | 并发 16 | 目标 |
  |---|---|---|---|
  | `/api/v1/me` | 29.9 | 109.1 | 300 |
  | `/api/v1/workspaces` | 28.9 | 193.6 | 300 |
  | `/api/v1/tasks`（200 条） | 57.3 | 273.1 | 300 |
  | `/api/v1/calendar/events`（本周 40 个） | 42.7 | 168.3 | 300 |
  | `/api/v1/notifications`（50 条） | 51.3 | 265.2 | 300 |
  | `/api/v1/dashboard/overview` | 124.6 | **565.4** | 500 |

  每组 200 次请求，都没有错误。

- **未达标项**：并发 16 时首页聚合的 P95 是 565 ms，超过 500 ms。
  - 原因是吞吐上限：单个进程每秒约能完成 39 次聚合。16 个请求同时到达时，平均排队约 16 / 39 ≈ 410 ms（实测 P50 388 ms）。
  - 并发 4（一个用户打开页面时浏览器同时发出的请求数量级）P95 是 125 ms，远低于目标。
  - 进一步改进要多进程运行 api。但进程内的状态（SSE 连接数限制、服务状态缓存、HTTP 指标）要先移到 Redis 或改为多进程汇总，
    改动面较大，不在 T-0044 范围内；验收报告如实记录。

### 8.7 CI

- `.github/workflows/ci-security.yml`：`deps`、`secrets`、`images` 三个 job 并行，不拖慢 ci-infra；每周一 02:23（UTC）另跑一次。
  - 用到的 Action 版本：`actions/checkout@v5`、`actions/setup-node@v5`、`astral-sh/setup-uv@v10.3.0`，与现有工作流相同；
  - `actions/cache@v5`：tag 存在（`git ls-remote`），`action.yml` 的运行时是 `node24`。
  - trivy 的漏洞库目录按天缓存。
- `ci-infra.yml` 在 Sprint 5 浏览器验收之后运行 `make smoke-full` 与性能基准（`--tolerance 2`），表格写进运行的 Summary。
- 本机实测耗时：`deps` 约 6 秒；`secrets` 约 2 秒；`images` 约 2 分钟（含 8 个第三方镜像）；完整冒烟约 30 秒；性能基准约 45 秒。

