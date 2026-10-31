# 发布前的版本锁定与许可证核验（T-0045，S6-06）

- 状态：accepted（第 3 节的 Redis 许可证问题待人类决定，在此之前保持现状）
- 日期：2026-10-31
- 作者：claude（协作任务 T-0045；codex 暂停期间按 D-0007 独立完成）
- 结果汇总在 `docs/versions.md`。

## 1. 全部镜像改为 tag + digest

- 之前只写了 tag 的 5 个镜像，改为 tag + digest：
  - compose 文件与 `ci-api.yml` 里的 `postgres:16.15-alpine3.24`、`redis:7.4.12-alpine3.21`、`caddy:2.11.7-alpine`；
  - Dockerfile 的基础镜像 `python:3.12.15-slim-trixie`、`node:22.23.4-alpine3.24`。
  - 其余镜像在核验时已经锁定。
- 取值：本机拉取的 `RepoDigests`（多架构索引的 digest）。PostgreSQL 与 Redis 另和 Docker Hub API 返回的该 tag 的 digest 对照，一致。
- 原因：同一个 tag 会被上游重新推送，例如 `caddy:2.11.7-alpine` 在 2026-10-30 重新推送过，digest 从 `eb4d35b7…` 变成 `e70b164d…`。只写 tag 时，不同时间部署的不一定是同一个镜像。
  锁定 digest 后，升级变成显式的改动，要重新核验并跑验收。
- 实测：
  - 锁定后的 10 个引用都能拉取；
  - 重新构建 api 与 web 镜像成功；
  - `docker compose config` 通过；
  - 重建 postgres、redis、caddy 容器后，`make smoke` 与 `make smoke-full` 通过。
- 代价：上游发布安全修复时不会自动跟上。用每周一的 `ci-security.yml` 扫描与发布前 checklist 跟进（`docs/release-checklist.md`）。

## 2. 许可证的核验方法

镜像都没有 `org.opencontainers.image.licenses` 标签，所以按以下来源核验，都读的是锁定版本对应的 tag：

| 组件 | 来源 | 结论 |
|---|---|---|
| PostgreSQL 16.15 | `postgres/postgres` tag `REL_16_15` 的 `COPYRIGHT` | PostgreSQL License（宽松许可） |
| Redis 7.4.12 | `redis/redis` tag `7.4.12` 的 `LICENSE.txt` | 自 7.4 起为 RSALv2 或 SSPLv1，二选一（第 3 节） |
| Caddy 2.11.7 | `caddyserver/caddy` tag `v2.11.7` 的 `LICENSE` | Apache-2.0 |
| nginx 1.30.6 | `nginx/nginx` tag `release-1.30.6` 的 `LICENSE` | BSD-2-Clause |
| Python 3.12.15 | `python/cpython` tag `v3.12.15` 的 `LICENSE` | PSF License Version 2 |
| Node.js 22.23.4 | `nodejs/node` tag `v22.23.4` 的 `LICENSE` | MIT（另附捆绑的第三方许可） |
| Prometheus、Grafana、n8n、Vikunja、restic、gitleaks、trivy、pip-audit | 已有的核验 ADR | 见 `docs/versions.md` 的“核验”列 |
| Python 依赖 | PyPI 上对应版本的 `license_expression` / `license` | 见 `docs/versions.md` 第 2 节 |
| npm 依赖 | `package-lock.json` 里各包的 `license` 字段 | 见 `docs/versions.md` 第 3 节 |

- 方法：浅克隆对应 tag，只检出许可证文件。
- 文档网站被本环境的网络策略拒绝，GitHub 的 git 访问可用。

## 3. Redis 7.4 的许可证（待人类决定）

- 事实：`LICENSE.txt`（tag 7.4.12）写明，2024-03-20 起 7.4 及之后版本的代码采用双许可，用户在 RSALv2 与 SSPLv1 中任选其一。
  - RSALv2 的限制（“Limitations” 一节）：不得把 Redis 或修改版的功能作为服务提供给第三方，也不得以让第三方使用其功能的方式分发。
  - 两者都不是 OSI 认可的开源许可。
- 对本项目的影响：本项目是自托管、给自己或内部使用的工作台，Redis 只在内部网络里给 api / worker 用，不对外提供。按 RSALv2 可以使用。
- 规范第 17 节要求部署前确认各组件的许可。以下情况需要人类决定，claude 不自行更换：
  1. 保持 `redis:7.4.12`（RSALv2），并写明“只供自用或内部使用”；
  2. 换用 BSD 许可的替代实现；
  3. 换用提供 OSI 许可选项的更新版本。
  - 后两种都要重新核验版本、镜像与客户端兼容性（ARQ、redis-py、SSE 用的 Streams），再跑全部验收。
- 在人类决定之前保持选项 1，不改代码。

## 4. 其他发现

- `ci-web.yml` 的 `actions/setup-node` 写的是 `node-version: '22'`，会取 22 系列的最新小版本；
  `ci-infra.yml` 与 web 镜像用的是 `22.23.4`。只影响 CI 里的前端检查，不影响部署的镜像。
  - 发布 checklist 里加了一项“CI 与镜像的 Node 版本一致”。
  - 这次不改，留给需要升级 Node 时一并处理。
