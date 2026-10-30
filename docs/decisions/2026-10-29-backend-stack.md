# ADR：后端技术栈与版本锁定（Sprint 0）

- 状态：已采纳
- 日期：2026-10-29
- 作者：claude
- 相关：协作任务 T-0005；`docs/DEVELOPMENT_SPEC.md` 第 2 节

## 背景

规范第 2 节确定了后端选型（FastAPI / Pydantic 2 / SQLAlchemy 2 + Alembic / asyncpg / Redis / ARQ），
并要求依赖固定版本、第三方组件固定具体 tag。本 ADR 记录 Sprint 0 实际锁定的版本和查证来源。

## 决策

### 运行时与依赖管理

- Python **3.12**（`requires-python = ">=3.12,<3.13"`，`services/api/.python-version`）。规范要求 3.12+；先锁定 3.12 系列，与容器镜像一致。
- 依赖用 **uv** 管理：`pyproject.toml` 写兼容范围，`uv.lock` 锁定精确版本，CI 用 `uv sync --locked` 保证一致。

### Python 依赖（`uv.lock` 中的精确版本）

| 包 | 版本 | 用途 |
|---|---|---|
| fastapi | 0.162.0 | Web 框架（随附 starlette 1.10.0） |
| pydantic | 2.14.2 | 请求 / 响应模型 |
| pydantic-settings | 2.16.2 | 从环境变量读取配置 |
| sqlalchemy[asyncio] | 2.1.6 | ORM（async） |
| asyncpg | 0.32.0 | PostgreSQL 驱动 |
| alembic | 1.20.1 | 数据库迁移 |
| redis | 5.3.1 | Redis 客户端（版本上限见下文） |
| arq | 0.28.0 | 后台作业 |
| argon2-cffi | 25.1.0 | Argon2id 密码哈希（Sprint 1，T-0010） |
| email-validator | 2.3.0 | 邮箱格式校验（`pydantic[email]`，Sprint 1，T-0009） |
| cryptography | 50.0.2 | 第三方凭证的 AES-256-GCM 加密（`AESGCM`，Sprint 1，T-0011） |
| uvicorn[standard] | 0.54.0 | ASGI 服务器 |
| httpx | 0.28.1 | 访问第三方服务的 HTTP 客户端（Sprint 3 起为运行时依赖，T-0020）；测试里也用作 ASGI 客户端 |
| pytest / pytest-asyncio | 9.2.0 / 1.4.1 | 测试（开发依赖） |
| ruff / mypy | 0.17.3 / 2.4.1 | lint、格式化、类型检查（开发依赖） |

Argon2id 参数：t=3、m=64 MiB、p=4（RFC 9106 推荐的低内存配置，`app/domain/security.py`）；参数调整后，旧哈希会在用户下次登录时自动升级。

来源：2026-10-29 / 2026-10-30 查询 PyPI（`pip index versions <包名>`、`https://pypi.org/pypi/<包名>/json`）；
cryptography 50.0.2 于 2026-09-30 发布，`requires_python >= 3.9`，本地已验证 `AESGCM` 加解密与 `InvalidTag`。

### 基础服务镜像（CI 与 Compose 共用）

| 服务 | 镜像 tag | 说明 |
|---|---|---|
| PostgreSQL | `postgres:16.15-alpine3.24` | 规范要求 16+；本地测试同为 16.15 |
| Redis | `redis:7.4.12-alpine3.21` | 规范要求 7+ |

来源：Docker Hub 官方镜像 tag 列表（`hub.docker.com/v2/repositories/library/<镜像>/tags`），已在本地实际拉取并运行。

### ARQ 的风险与约束

- ARQ 官方 README 写明目前处于**仅维护（maintenance only）**状态，见 https://github.com/python-arq/arq/issues/510 。
- arq 0.28.0（2026-04-16 发布）的依赖声明为 `redis[hiredis]>=4.2.0,<6`，因此 redis-py 只能用 5.x（当前 5.3.1），不能升到 8.x。
- arq 0.28 内部仍调用 redis-py 已弃用的 `close()`，测试中会出现 DeprecationWarning（已在 pytest 配置中按来源过滤）。

按规范，Sprint 0 仍采用 ARQ，但：作业逻辑不依赖 ARQ 特有的 API；关键作业状态先写 PostgreSQL（规范第 2、10 节），
Redis 不当作唯一持久化队列。如果之后 ARQ 无法满足需要（例如需要 redis-py 6+），写新的 ADR 评估替代方案。

Sprint 3（T-0021）按这个约束落地：Webhook 事件先写 PostgreSQL 的 `webhook_inbox`，Worker 用 `FOR UPDATE SKIP LOCKED` 领取，
ARQ 只负责“尽快唤醒 Worker”和定时调度——投递丢了，每分钟的兜底轮询与每 5 分钟的对账仍会处理。
API 投递作业只经过 `app/jobs/queue.py` 的 `JobQueue`（与 API 共用 Redis 连接池的 `ArqRedis`），替换 ARQ 时只需改这一处与 Worker 入口。

## 后果

- 升级任何依赖：修改 `pyproject.toml` 范围 → `uv lock` → 运行 `make lint`、`make test`（含集成测试）→ 更新本 ADR 的版本表。
- 镜像 tag 变更同时更新 `.github/workflows/ci-api.yml` 与 `compose.yaml`。
