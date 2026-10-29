# Sprint 0 验收报告

- 验收人：claude（协作任务 T-0006，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30（北京时间）
- 结论：**Sprint 0 的 DoD 全部通过**；下列“未运行”项不属于 Sprint 0 范围或受环境限制，已逐条说明。
- 是否进入 Sprint 1：等待人类批准（docs/CODEX_START.md：“未经用户批准，不进入 Sprint 1”）。

## 1. Sprint 0 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| `make up` 起服务 | 通过 | 本地：6 个服务启动，postgres / redis / api / worker / web 均 `healthy`；CI（PR #10，`ci-infra` / compose）：在干净的 GitHub runner 上从零构建镜像，`make up` → `make migrate` → `make smoke` → 停止后重启并确认数据卷保留，全部成功 |
| `/healthz` 200 | 通过 | 经 Caddy：`200 {"status":"ok"}`（`make smoke`） |
| Web 首页可打开 | 通过 | 无头 Chromium 打开 `/`、`/tasks`、`/settings/security`：均 200，标题分别为“总览”“任务”“安全设置”，导航 12 项；切换暗色主题生效；在 CSP 下控制台无错误、无失败请求 |
| `make test` 通过 | 通过 | 后端 `18 passed`（含 4 个连接真实 PostgreSQL 16.15 / Redis 7 的集成测试）；前端 `Tests 18 passed` |
| `make lint` 通过 | 通过 | ruff、ruff format、mypy（strict）、ESLint（0 warnings）、vue-tsc 全部通过 |

## 2. 任务清单（docs/TASKS.md Sprint 0）

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S0-01 目录结构、README、.env.example、.gitignore、Makefile | T-0005（PR #5），T-0006 补充 Compose 相关命令 | `make help` 列出规范第 11.3 节全部目标；`make backup` / `restore-verify` 明确提示尚未实现（S6-04） |
| S0-02 Vue3 + TS + Vite、路由、主题、布局、Vitest | T-0004（codex，PR #7），claude 审查并复验 | `npm ci / lint / typecheck / test(18) / build` 全部通过；Vite 代理与 history 回退经实测 |
| S0-03 FastAPI / Pydantic 2 / SQLAlchemy 2、`/healthz`、`/readyz`、pytest | T-0005（PR #5） | `/readyz` 在依赖正常时 200、Redis 不可达时 503（集成测试） |
| S0-04 PostgreSQL / Redis / Core Compose、持久化、健康检查 | T-0006（本次） | `compose.yaml`；`make down` 后数据卷保留，重启后 `alembic_version = 0001` |
| S0-05 Alembic、空库迁移与初始化冒烟 | T-0005（PR #5），T-0006 在容器中复验 | 空库 upgrade → downgrade → upgrade（pytest 与 CI）；`make migrate` 在 api 容器内成功 |
| S0-06 CI（lint / typecheck / test / build / compose config）、运行手册 | T-0005（ci-api）、T-0004（ci-web）、T-0006（ci-infra、`docs/deployment.md`） | 三个工作流在 PR 与 main 上运行 |

## 3. 交付完成定义（docs/DEVELOPMENT_SPEC.md 第 18 节）

| 条件 | 结果 | 说明 |
|---|---|---|
| 代码已提交到版本控制 | 通过 | PR #5、#7、#10 |
| 所有必需依赖固定版本 | 通过 | `uv.lock`、`package-lock.json`；镜像全部固定 tag（见集成 ADR），无 `latest` |
| 开发和部署命令可复现 | 通过 | `make up / migrate / smoke` 在本地与 CI 运行；`make env` 生成随机密钥，不依赖手工填写 |
| 迁移可执行 | 通过 | 本机（非超级用户 owner）、CI service container、Compose 容器内三处执行 |
| 自动测试通过 | 通过 | 后端 18、前端 18；CI：ci-api、Web CI、ci-infra |
| 手工 Smoke Test 通过 | 通过 | `make smoke` 9 项；浏览器打开首页并切换主题 |
| 关键安全测试完成 | 部分（符合 Sprint 0 范围） | 已测：错误响应不泄露细节、request_id 校验、非 root 运行、DB / Redis 未映射端口、安全响应头。CSRF / IDOR / SSRF 等随 Sprint 1、3 的功能一起实现和测试 |
| README / 配置示例更新 | 通过 | README 快速开始、`docs/deployment.md`、`.env.example` |
| 未实现能力明确标注 | 通过 | 前端所有业务页标注“规划中”；`make backup` / `restore-verify` 提示 S6-04；各文档的“当前状态”列 |
| 无明文密钥 | 通过 | 仓库内只有占位符；`.env` 被 git 忽略、权限 600 |
| 无未经验证的第三方 API 假设 | 通过 | Sprint 0 不连接任何第三方服务 |

## 4. 未运行 / 限制

| 项目 | 原因 |
|---|---|
| 公网来源访问 `/readyz` 返回 404 | 只能从内网测试（返回 200）；规则见 Caddyfile，属配置层面保证 |
| HTTPS / HSTS | 开发环境只用 HTTP；生产启用步骤写在 `docs/deployment.md` 第 6 节 |
| 真实移动设备与辅助技术（读屏）测试 | 仅有 jsdom 行为测试与桌面无头浏览器检查 |
| 备份与恢复演练 | 规范安排在 S6-04 |
| 本地 `docker build` 的网络 | 验收所在的云端环境出网需经代理：本地构建时额外传入代理与 CA（只在构建阶段，运行镜像中不残留，已检查）。仓库里的 Dockerfile 未做任何环境相关修改，CI 中按原样构建 |

## 5. 验收时执行的命令（摘录）

```
$ make up                      # 本地镜像已构建，执行 docker compose up -d --wait
 Container my-workspace-postgres-1 Healthy
 Container my-workspace-redis-1 Healthy
 Container my-workspace-api-1 Healthy
 Container my-workspace-worker-1 Healthy
 Container my-workspace-web-1 Healthy
 Container my-workspace-caddy-1 Healthy

$ make migrate
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, 创建 workspace schema 与 pgcrypto、pg_trgm 扩展

$ make smoke
通过  API 存活（/healthz → 200）
通过  API 就绪（数据库 + Redis）（/readyz → 200）
通过  统一错误格式（/api/v1/nope → 404）
通过  Web 首页（/ → 200）
通过  前端路由回退（/settings/security → 200）
通过  响应头 X-Content-Type-Options
通过  响应头 Content-Security-Policy
通过  响应头 X-Frame-Options
通过  不暴露 Server 头
冒烟检查全部通过

$ make down && docker compose up -d --wait   # 数据卷保留
$ psql … -c "select version_num from alembic_version"
0001

$ make lint
All checks passed! / Success: no issues found in 27 source files / eslint、vue-tsc 通过

$ make test
18 passed（后端，REQUIRE_INTEGRATION=1）
Tests  18 passed (18)（前端）
```

运行镜像检查：api 以 `uid=10001(app)`、web 以 `uid=101(nginx)` 运行；镜像内不含构建期的代理证书和代理环境变量；生产镜像不含 pytest 等开发依赖。
