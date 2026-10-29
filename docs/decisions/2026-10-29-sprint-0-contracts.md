# ADR：Sprint 0 前后端并行开发的接口契约

- 状态：已采纳
- 日期：2026-10-29
- 作者：claude
- 相关：`.collab/decisions/D-0003-module-ownership.md`；协作任务 T-0004（codex，前端）、T-0005（claude，后端与骨架）、T-0006（claude，集成与验收）

## 背景

Sprint 0 由 codex 做前端、claude 做后端和平台，两边同时开工。只要先约定好下面这些接口，
两边就不需要等对方，也不会改同一批文件。

## 决策

### 1. 目录归属

| 路径 | owner |
|---|---|
| `apps/web/**`（`Dockerfile`、`.dockerignore` 除外） | codex |
| `.github/workflows/ci-web.yml` | codex |
| `services/api/**`、`infra/**`、`compose*.yaml` | claude |
| `apps/web/Dockerfile`、`apps/web/.dockerignore` | claude |
| 根目录 `Makefile`、`README.md`、`.env.example`、`.gitignore` | claude |
| `.github/workflows/ci-api.yml`、`.github/workflows/ci-infra.yml` | claude |
| `docs/architecture.md`、`docs/api-contract.md`、`docs/data-dictionary.md`、`docs/deployment.md`、`docs/threat-model.md` | claude |
| `docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md`、`docs/CODEX_START.md` | human（`TASKS.md` 由 Sprint 验收任务勾选） |
| `docs/decisions/` | 谁做决定谁新增文件，不改别人的；文件名 `YYYY-MM-DD-英文短名.md`，避免两边编号冲突 |
| `.collab/`、`scripts/collab*.py`、`.github/workflows/collab*.yml` | 协作设施，按 AGENTS.md 1–7 节修改 |

需要改别人目录里的东西：在对方任务日志里 `log`，或者新建任务交给对方。

### 2. 前端工程（codex 实现，claude 负责容器打包）

- Node.js 22 LTS；包管理用 **npm**，提交 `apps/web/package-lock.json`。
- `apps/web/package.json` 必须提供下面这些 scripts。根目录 `Makefile` 和 CI 只调用这些名字：

  | script | 要求 |
  |---|---|
  | `dev` | Vite 开发服务器，端口 5173 |
  | `build` | 生产构建，产物在 `apps/web/dist/`（纯静态 SPA） |
  | `lint` | ESLint，只检查不自动修复，有问题时非零退出 |
  | `typecheck` | `vue-tsc --noEmit` |
  | `test` | Vitest 单次运行（不是 watch 模式） |

- 浏览器只用**相对路径**访问后端：`/api/v1/...`、`/healthz`、`/readyz`。
  开发时 `vite.config.ts` 把这三个前缀代理到 `http://localhost:8000`；生产环境由 Caddy 同源转发。
  前端不保存、不读取任何第三方 Token 或 Secret。
- 路由用 HTML5 history 模式；回退到 `index.html` 由 Caddy / 静态服务负责。
- Sprint 0 不需要任何构建期环境变量；以后如需，只用 `VITE_` 前缀，且不能放密钥。

### 3. 后端工程（claude）

- Python 3.12；用 uv 管理依赖并提交 `services/api/uv.lock`。应用入口 `services/api/app/main.py` 中的 `app`，容器内监听 8000 端口。
- `GET /healthz`：只表示进程存活，不检查依赖。返回 `200 {"status": "ok"}`。
- `GET /readyz`：检查 PostgreSQL 与 Redis。
  全部正常时返回 `200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`；
  任一失败时返回 `503`，`status` 为 `"unavailable"`，失败项的值为 `"error"`。
- 业务接口前缀 `/api/v1`，统一错误格式见 `docs/DEVELOPMENT_SPEC.md` 第 6 节。
- 从 Sprint 1 起，后端提交由代码生成的 `services/api/openapi.json`（`make openapi`，CI 检查它是否过期），
  前端用它生成 TypeScript 类型，不手抄字段（D-0003 第 2 条）。

### 4. 根目录命令（claude 实现）

`Makefile` 提供 `docs/DEVELOPMENT_SPEC.md` 第 11.3 节的全部目标。其中 `lint`、`test` 同时覆盖前后端：
前端部分调用 `npm --prefix apps/web run <script>`；`apps/web` 尚不存在时跳过并打印提示，所以两边可以各自先合并。

### 5. 运行时拓扑（T-0006 实现）

- Caddy 对外监听 `8080`（与 `.env.example` 中 `PUBLIC_BASE_URL=http://localhost:8080` 一致）：
  `/api/*`、`/healthz`、`/readyz` 转发到 `api:8000`，其余请求交给 `web`（静态 SPA）。
- `postgres`、`redis` 不映射到宿主机端口；所有镜像固定具体版本号，不用 `latest`。

### 6. CI

- 每个 owner 维护自己的 workflow 文件，互不修改：`ci-web.yml`（codex）、`ci-api.yml` / `ci-infra.yml`（claude）。
- `ci-web.yml` 在 `pull_request` 和推送到 `main` 时运行，工作目录 `apps/web`：
  `npm ci` → `npm run lint` → `npm run typecheck` → `npm run test` → `npm run build`；
  用 `actions/setup-node` 固定 Node 22，并开启 npm 缓存。

## 后果

- 前后端在 Sprint 0 可以完全并行，唯一的连接点就是上面这些路径、命令名和接口。
- 修改本契约需要两边都知道：在 PR 里写明，并新增一份 ADR 说明取代了哪一条。
