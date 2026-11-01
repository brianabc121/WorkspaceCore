# 版本表

> 状态：2026-10-31（Sprint 6，T-0045）。
> 这里集中列出所有镜像与关键依赖的锁定版本、许可证和核验来源。
> 升级任何一项都要按项目规则先核验（官方文档或运行实例），同时更新这张表和对应的 ADR，再跑全部验收（`docs/release-checklist.md`）。
> 许可证的核验方法与来源见 `docs/decisions/2026-10-31-release-versions-and-licenses.md`。

## 1. 容器镜像

所有镜像都写成 tag + digest（生产禁止浮动 tag）。digest 是多架构索引的 digest，与 Docker Hub 上该 tag 的 digest 一致。

### 1.1 核心服务（`compose.yaml`）

| 组件 | 镜像 | digest | 许可证 | 核验 |
|---|---|---|---|---|
| PostgreSQL 16.15 | `postgres:16.15-alpine3.24` | `sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab` | PostgreSQL License | `2026-10-29-backend-stack.md`、`2026-10-30-sprint-0-integration.md` |
| Redis 7.4.12 | `redis:7.4.12-alpine3.21` | `sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f` | **RSALv2 或 SSPLv1**（见第 5 节） | 同上 |
| Caddy 2.11.7 | `caddy:2.11.7-alpine` | `sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5` | Apache-2.0 | `2026-10-30-sprint-0-integration.md` |
| api / worker | `my-workspace-api:local`（本仓库构建，`services/api/Dockerfile`） | — | — | 见 1.3 节 |
| web | `my-workspace-web:local`（本仓库构建，`apps/web/Dockerfile`） | — | — | 见 1.3 节 |

### 1.2 可选与独立部署的组件

| 组件 | 镜像 | digest | 许可证 | 在哪里 | 核验 |
|---|---|---|---|---|---|
| Prometheus 3.15.1 | `prom/prometheus:v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` | Apache-2.0 | `compose.yaml`（`--profile monitoring`） | `2026-10-31-prometheus-grafana-n8n-verification.md` |
| Grafana 13.2.5 | `grafana/grafana:13.2.5` | `sha256:9f7aa1e1e3246aade5792d35a738049f7f32f89cd51d1956cf426a4e86268a84` | AGPL-3.0 | 同上 | 同上 |
| Vikunja 2.7.1 | `vikunja/vikunja:2.7.1` | `sha256:f7dc8428d10aef17f3075886d3c1c022f566ec456f88985f02c137f4fc534576` | AGPL-3.0-or-later | `infra/vikunja/compose.yaml` | `2026-10-30-vikunja-2.7.1-verification.md` |
| n8n 2.45.7 | `n8nio/n8n:2.45.7` | `sha256:9a8d8dcea75667496695d7810d7e1fe018a65efb36cc499d347f3931b3957b5d` | Sustainable Use License（`.ee` 文件另有企业许可） | `infra/n8n/compose.yaml` | `2026-10-31-prometheus-grafana-n8n-verification.md`、`2026-10-31-n8n-trigger-and-execution-verification.md` |
| Vikunja / n8n 的数据库 | `postgres:16.15-alpine3.24` | 同 1.1 节 | PostgreSQL License | 两个 infra compose 文件 | — |
| AFFiNE | 不随本仓库部署 | — | `packages/backend` 与原生模块以外为 MIT；服务端为 AFFiNE Enterprise Edition 许可（CE 部分 MPL 2.0） | 用户自行部署，工作台只存链接 | `2026-10-30-github-rest-and-affine-verification.md` 第 7 节 |

### 1.3 本仓库镜像的基础镜像与构建工具

| 用途 | 镜像 / 工具 | digest | 许可证 | 核验 |
|---|---|---|---|---|
| api / worker 运行与构建 | `python:3.12.15-slim-trixie` | `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` | PSF-2.0 | `2026-10-30-sprint-0-integration.md` |
| api 依赖安装 | `uv==0.12.24`（构建阶段用 pip 安装） | — | MIT OR Apache-2.0 | 同上 |
| web 构建阶段（不进最终镜像） | `node:22.23.4-alpine3.24` | `sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914` | MIT | 同上 |
| web 运行 | `nginxinc/nginx-unprivileged:1.30.6-alpine3.24-slim` | `sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6` | BSD-2-Clause（nginx） | `2026-10-31-sprint-6-backup-and-scan-verification.md` 第 8.3 节 |

### 1.4 运维与 CI 工具

| 工具 | 版本 / 镜像 | digest | 许可证 | 用途 | 核验 |
|---|---|---|---|---|---|
| restic | `restic/restic:0.19.1` | `sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510` | BSD-2-Clause | `make backup` / `make restore-verify` | `2026-10-31-sprint-6-backup-and-scan-verification.md` 第 3 节 |
| gitleaks | `zricethezav/gitleaks:v8.30.1` | `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` | MIT | `make scan`（secrets） | 同上，第 5.3 节 |
| trivy | `aquasec/trivy:0.76.1` | `sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c` | Apache-2.0 | `make scan`（images） | 同上，第 5.4 节 |
| pip-audit | `pip-audit==2.10.1`（`uvx` 临时运行） | — | Apache-2.0 | `make scan`（deps） | 同上，第 5.1 节 |
| Playwright | `playwright@1.63.0`（`tests/e2e/package-lock.json`） | — | Apache-2.0 | 浏览器验收与 `make smoke-full` | `docs/acceptance/sprint-2.md` 起各验收报告 |
| GitHub Actions | `actions/checkout@v5`、`actions/setup-node@v5`、`astral-sh/setup-uv@v10.3.0`、`actions/cache@v5` | — | — | `.github/workflows/` | `actions/cache` 见第 8.7 节（同上） |

CI 的 `ci-api.yml` 用与 1.1 节相同的 PostgreSQL、Redis 镜像（同一个 digest）。

### 1.5 V2：OpenClaw 与 OpenIM（OC-0 核验，OC-3 ~ OC-7 已部署并验收；`docs/deployment.md` 第 17、18 节）

来源与说明见 `2026-10-31-v2-oc0-openclaw-openim-verification.md`。
OpenIM 的官方镜像有越权问题，已换成自建的补丁镜像（`2026-10-31-v2-oc3-openim-patched-image.md`）。自建镜像每次构建的 digest 不同，所以锁定的是全部构建输入。

| 组件 | 镜像 / 包 | digest / 校验值 | 许可证 | 在哪里 |
|---|---|---|---|---|
| OpenClaw 2026.9.36（extended-stable） | `openclaw/openclaw:2026.9.36` | `sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f` | MIT | 各 OpenClaw 服务器（`infra/openclaw/`） |
| OpenIM 渠道插件 0.3.3 | `@openim/openclaw-channel@0.3.3`（npm） | tarball sha256 `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643` | AGPL-3.0-only | 同上 |
| OpenIM Server v3.8.3-patch.18 **补丁镜像** | `my-workspace/openim-server:v3.8.3-patch.18-myws.2`（本机构建，不推送） | 上游提交 `120cee6d5783959a02ea1acfa414589fe5aae90f` + `infra/openim/patches/` | Apache-2.0 | `infra/openim/compose.yaml`；构建 `infra/openim/server/build.sh` |
| 构建镜像 Go 1.26.9 | `golang:1.26.9-alpine3.24` | `sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade` | BSD-3-Clause | `infra/openim/server/Dockerfile`（只用于构建） |
| 运行镜像 Alpine 3.24.2 | `alpine:3.24.2` | `sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6` | MIT（busybox 等为 GPL-2.0） | 同上（补丁镜像的基础） |
| mage v1.17.2 | `github.com/magefile/mage@v1.17.2`（Go 模块） | Go 模块校验 | Apache-2.0 | 同上（OpenIM 的构建与启动工具） |
| OpenIM Server v3.8.3-patch.18（官方，**有越权问题，不再使用**） | `openim/openim-server:v3.8.3-patch.18` | `sha256:71d5872be4e603c94a3a06d4279bac2b3b5aa66685e95403efac7489690773e1` | Apache-2.0 | 只在 OC-0 核验中用过 |
| MongoDB 7.0.28 | `mongo:7.0.28` | `sha256:4510cf3d7050003e958745adb25d2deb3fb907430716162d9cc1a92eda2a6047` | SSPL-1.0 | 同上 |
| Redis 7.0.0（OpenIM 用） | `redis:7.0.0` | `sha256:1b90dbfe6943c72a7469c134cad3f02eb810f016049a0e19ad78be07040cdb0c` | BSD-3-Clause | 同上 |
| Kafka 3.9.2（官方，KRaft，SASL/PLAIN） | `apache/kafka:3.9.2` | `sha256:05b4616e0702ef2729327705d54ad6b50ea70b271c4b730fabd2320789fb7b02` | Apache-2.0 | 同上；不用 4.x，见 OC-3 ADR 第 7.3 节 |
| Garage v2.4.1（对象存储，替代 MinIO） | `dxflrs/garage:v2.4.1` | `sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020` | AGPL-3.0 | 同上；初始化由 `garage-init`（`alpine:3.24.2`）完成，见 OC-3 ADR 第 8 节 |
| etcd 3.5.34（官方，开启认证） | `gcr.io/etcd-development/etcd:v3.5.34` | `sha256:04726053dabaf26b56ce7ef28c3d20b8b4a4006ed887d30194aa4f0756ee4f8d` | Apache-2.0 | 同上；认证由 `etcd-init`（`alpine:3.24.2`）开启 |
| MCP Python SDK 2.3.0 | `mcp==2.3.0`（PyPI） | — | MIT | OC-1 起加入 `services/api` 依赖 |
| ghostunnel v1.11.4 | `ghostunnel/ghostunnel:v1.11.4` | `sha256:e5248233863aadd36383a11c92360f2981141881ef05e38360a6ada8630d36b1` | Apache-2.0 | 外网模式 OpenClaw 服务器的本机隧道（接入包的 `compose.yaml`；`2026-10-31-v2-oc2-agent-ingress-verification.md`） |

## 2. 后端依赖（`services/api/uv.lock`）

`uv sync --locked` 按锁文件安装，`uv.lock` 同时锁定所有传递依赖及其哈希。下面只列 `pyproject.toml` 里直接声明的依赖。

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| fastapi | 0.162.0 | MIT | Web 框架 |
| pydantic | 2.14.2 | MIT | 模型与校验 |
| pydantic-settings | 2.16.2 | MIT | 配置 |
| sqlalchemy | 2.1.6 | MIT | ORM |
| asyncpg | 0.32.0 | Apache-2.0 | PostgreSQL 驱动 |
| alembic | 1.20.1 | MIT | 迁移 |
| argon2-cffi | 25.1.0 | MIT | 密码哈希 |
| redis（客户端） | 5.3.1 | MIT | Redis 客户端 |
| arq | 0.28.0 | MIT | 后台任务 |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI 服务器 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | 凭证加密 |
| httpx | 0.28.1 | BSD-3-Clause | 出站 HTTP |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | 自身指标 |
| 开发：pytest / pytest-asyncio / ruff / mypy | 9.2.0 / 1.4.1 / 0.17.3 / 2.4.1 | MIT / Apache-2.0 / MIT / MIT | 测试与检查 |

Python 运行时：3.12（`requires-python = ">=3.12,<3.13"`，镜像里是 3.12.15）。

## 3. 前端依赖（`apps/web/package-lock.json`）

`npm ci` 按锁文件安装。下面只列 `package.json` 里直接声明的依赖。

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| vue | 3.5.44 | MIT | 框架 |
| vue-router | 4.6.4 | MIT | 路由 |
| pinia | 3.0.4 | MIT | 状态 |
| @tanstack/vue-query | 5.105.0 | MIT | 服务端数据缓存 |
| openapi-fetch | 0.17.0 | MIT | 强类型 API 客户端 |
| vee-validate | 4.15.1 | MIT | 表单 |
| zod | 4.6.7 | MIT | 校验（本地适配见 `apps/web/src/services/forms.ts`） |
| gridstack | 14.0.0 | MIT | 首页拖拽布局 |
| 开发：vite / vitest / typescript / vue-tsc | 7.3.7 / 4.1.12 / 5.9.3 / 3.3.14 | MIT / MIT / Apache-2.0 / MIT | 构建、测试、类型检查 |
| 开发：eslint / typescript-eslint / eslint-plugin-vue | 9.39.5 / 8.72.1 / 10.12.0 | MIT | 代码检查 |
| 开发：tailwindcss / @tailwindcss/vite | 4.3.3 | MIT | 样式 |
| 开发：openapi-typescript | 7.13.0 | MIT | 由 `openapi.json` 生成类型 |
| 开发：jsdom / @testing-library/vue / @testing-library/jest-dom | 26.1.0 / 8.1.0 / 6.9.1 | MIT | 组件测试 |

Node 运行时：22（`engines` 要求 ≥ 22.12；镜像里是 22.23.4）。锁文件用 npm 11.6.2 生成，npm 10 的 `npm ci` 可以正常安装（`2026-10-31-sprint-6-backup-and-scan-verification.md` 第 8.2 节）。

## 4. 数据库结构版本

- Alembic 迁移到 `0010`（`services/api/migrations/versions/`），对象清单见 `docs/data-dictionary.md`。
- 每次备份的 `backup-info.json` 记录当时的提交号与迁移版本。

## 5. 许可证注意事项

- **AGPL-3.0（Grafana、Vikunja）**：作为独立服务原样运行，工作台只通过 HTTP API 使用或生成外链，不修改也不链接它们的代码。修改它们并对外提供服务时，要按 AGPL 公开修改。
- **n8n（Sustainable Use License）**：允许自托管用于内部业务。对外商业托管、或让其他客户配置工作流之前，要重新核验许可证（规范 7.5）。
- **Redis 7.4（RSALv2 或 SSPLv1，二选一）**：Redis 从 7.4 起不再是 BSD 许可。
  - RSALv2 的限制是不得把 Redis 的功能作为服务提供给第三方。
  - 本项目自托管、只给自己或内部使用，不对外提供 Redis，所以按 RSALv2 可以使用。
  - 如果以后要对外分发或托管，需要人类决定是否换用 BSD 许可的替代品，或换用提供 OSI 许可选项的版本。
  - 这件事已记入 `docs/decisions/2026-10-31-release-versions-and-licenses.md` 第 3 节，等人类确认。
- **AFFiNE**：不随本仓库分发；用户自行部署时自行确认许可（规范第 17 节）。
- 其余镜像与依赖都是 MIT、BSD、Apache-2.0、PSF、PostgreSQL 这类宽松许可。

## 6. 升级一个版本

1. 先核验：读新版本的官方文档或发布说明，在本机跑一遍，把结论写进对应的 ADR（项目规则 3）。
2. 改 tag + digest（`docker pull <镜像>:<tag>` 后，`docker image inspect --format '{{index .RepoDigests 0}}' <镜像>:<tag>` 取 digest）。
3. 更新这张表。
4. 运行 `make scan`，跑全部验收；按 `docs/release-checklist.md` 发布。
5. PostgreSQL 换大版本（例如 16 → 17）不能只换镜像：数据目录不兼容，要先备份，再用 `pg_dump` / `pg_restore` 迁移（`docs/deployment.md` 第 14 节）。
