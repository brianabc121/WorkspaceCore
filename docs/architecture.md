# 架构说明

> 状态：Sprint 6（2026-11-01，含 V2 的 OpenClaw / OpenIM）。目标架构见 `docs/DEVELOPMENT_SPEC.md` 第 3、4 节与 `docs/v2/TRANSFORMATION_PLAN.md` 第 3 节；本文记录**已经实现**的部分，随 Sprint 推进补充。
> 接口约定见 `docs/api-contract.md`，数据库对象见 `docs/data-dictionary.md`，运行与部署见 `docs/deployment.md`，版本见 `docs/versions.md`。

## 运行组件

| 组件 | 位置 | 当前状态 |
|---|---|---|
| API（FastAPI BFF） | `services/api/app/main.py` | 认证与会话、工作空间与角色、仪表盘与组件、任务（Vikunja）、知识库收藏与关联、日历、GitHub 只读视图、跨资源搜索、运维状态与指标、通知、SSE 事件流、n8n 白名单触发、审计；V2：OpenClaw 实例与接入包、密钥库、IM（经 OpenIM 中转）、MCP 工具与审批；`/healthz`、`/readyz`、`/metrics`（只在内部网络，Caddy 不转发）。实例只能经接入入口到达 `/agent-api/*`、`/mcp`、`/internal/gateway/*` |
| Worker（ARQ） | `services/api/app/jobs/worker.py` | 心跳（每分钟）、Webhook Inbox 处理、增量与全量对账（Vikunja、GitHub）、服务状态检查、自动化执行记录对账、过期数据清理；V2：OpenIM 回调收件箱处理、IM 增量（30 秒）与全量（每天）对账、实例 IM 账号补注册、审批卡片发送、待审批过期 |
| 数据库迁移（Alembic） | `services/api/migrations/` | `0001` ~ `0018`，都只新增对象、都有 downgrade（`docs/data-dictionary.md`） |
| 前端（Vue 3） | `apps/web/` | 首页（GridStack 拖拽布局、组件库）、任务与项目、知识库、日历、开发者、运维、自动化、设置、集成设置、安全设置、审计日志、通知中心；V2：IM（单聊、群聊、置顶、免打扰、分组、@）、通讯录、OpenClaw 实例、密钥库、AI 审批与活动记录；右侧可收起的 AI / 详情抽屉（AI 助手、Task Drawer）。UI 组件：Tailwind 4 + shadcn-vue（`src/components/ui`，`2026-11-01-shadcn-vue-and-drawer.md`） |
| 统一入口（Caddy） | `infra/caddy/Caddyfile`、`https.Caddyfile`、`routes.caddy` | 路由、安全响应头、压缩（SSE 除外）、请求体限制；`/readyz` 仅内网。默认 HTTP :8080；HTTPS 域名模式（`compose.https.yaml`）自动申请证书并加 HSTS |
| core 编排 | `compose.yaml` | web、api、worker、postgres、redis、caddy，全部带健康检查；可选监控（`--profile monitoring`：Prometheus、Grafana） |
| 独立部署的组件 | `infra/vikunja/`、`infra/n8n/`、`infra/openim/` | Vikunja（任务主源）、n8n（自动化，可选）、OpenIM（V2 的 IM 后端：打过补丁的 openim-server、MongoDB、Redis、etcd、Kafka、Garage 对象存储），各自的 Compose 项目，通过 `integrations` / `im` / `agent-im` 网络与工作台互通 |
| 接入入口（V2，`--profile agents`） | `infra/caddy/agent.Caddyfile` | agent-gateway：内网入口与外网入口（双向证书）两个站点，只放行实例要用的路径；`/im/ws` 经 API 的 forward_auth 校验后透传到 OpenIM 消息网关 |
| OpenClaw 实例（V2，别的服务器） | 接入包（`services/api/app/agent_bundle/`） | 每台一份接入包：OpenClaw（锁定版本）+ `openclaw-channel` 插件、取密钥小程序、启动包装脚本；外网模式另有本机隧道（ghostunnel） |
| 运维脚本 | `scripts/` | 冒烟、备份与隔离恢复（restic）、安全扫描 |

## 数据流要点

- **任务以 Vikunja 为主源**（规范第 4 节）：工作台的写入经 Connector 发给 Vikunja（带 `Idempotency-Key`，结果未知时不自动重试），
  成功后更新本地投影；读取走本地投影。Vikunja 的 Webhook 进 Inbox 由 Worker 处理，周期对账兜底。
- **AFFiNE 只做深链接**与收藏，不读写文档内容；**GitHub 只读**投影（ETag、限流感知）。
- **首页聚合**只读本地投影与缓存，各模块并发、有各自的超时与总时限；单个模块失败只让它自己降级。
- **实时更新**：数据变化在事务提交后发布到 Redis Stream，SSE 只发“数据失效”提示，前端据此重新拉取。
- **第三方故障可降级**：出站 HTTP 有超时、有限次重试、按集成计数的熔断，防 SSRF 与 DNS 重绑定（`docs/threat-model.md`）。

## 后端分层（`services/api/app/`）

| 目录 | 职责 | 约束 |
|---|---|---|
| `api/` | HTTP 路由 | 不写业务逻辑；业务逻辑不依赖路由函数 |
| `domain/` | 领域服务、实体、权限策略 | 所有读写都做 workspace scope + role 校验 |
| `repositories/` | SQLAlchemy 数据访问 | SQL 集中在这里 |
| `integrations/` | Connector 协议与实现 | 只处理外部协议与归一化；外部 HTTP 必须有 timeout |
| `jobs/` | Worker、对账 | 关键作业状态先写 PostgreSQL |
| `models/` | ORM 模型 | 表放在 `workspace` schema，主键 UUID |
| `schemas/` | Pydantic 请求 / 响应 | API schema 先行 |
| `core/` | 配置、日志、错误、中间件、共享资源 | 密钥类配置用 `SecretStr` |

## 请求处理

1. `request_context` 中间件分配 `request_id`（接受合法的 `X-Request-ID`，否则生成 `req_<uuid>`），并写入响应头和每条日志。
2. 路由处理请求；`HTTPException` 与参数校验错误转换为统一错误格式。
3. 未处理异常在中间件内兜底为 500，响应中不包含异常细节。
4. 访问日志只记录方法、路径（不含查询参数）、状态码和耗时；`/healthz`、`/readyz` 成功的请求记为 DEBUG，避免容器健康检查刷屏。

## 部署拓扑（`compose.yaml`）

```mermaid
flowchart LR
    U[浏览器] -->|127.0.0.1:8080| C[caddy]
    subgraph frontend[frontend 网络]
        C -->|/api/* /healthz /readyz| A[api :8000]
        C -->|其余路径| W[web :8080 nginx]
    end
    subgraph backend[backend 网络（internal）]
        A --> P[(postgres)]
        A --> R[(redis)]
        K[worker] --> P
        K --> R
    end
    subgraph integrations[integrations 网络（internal）]
        A --> V[vikunja]
        K --> V
        A --> N[n8n]
        K --> N
        V -->|Webhook| C
    end
    subgraph monitoring[monitoring 网络（internal，可选）]
        PR[prometheus] -->|/metrics| A
        A --> PR
    end
    K -->|egress 网络| G[GitHub 等外部服务]
```

- 对外只有 caddy 一个端口（默认只绑定 127.0.0.1）；postgres、redis 只在内部网络，不映射宿主机端口。
- `integrations` 网络连接独立部署的 Vikunja、n8n；Vikunja 的 Webhook 经 caddy 回到 api。
- worker 经 `egress` 网络访问外部服务；api 访问 GitHub 等外部服务时同样受出站策略限制（SSRF 防护、DNS 固定）。
- Grafana（可选）另有 `monitoring-public` 网络，用于浏览器访问它自己的端口；工作台只生成外链。
- api 与 worker 使用同一个镜像（`services/api/Dockerfile`），以非 root 用户、只读文件系统运行。
- web 镜像（`apps/web/Dockerfile`）用 Node 22 构建，nginx（非 root，slim 变体）提供静态文件与 history 回退。
- 所有镜像按 tag + digest 锁定（`docs/versions.md`）。

## V2：OpenClaw 与 OpenIM（`docs/v2/TRANSFORMATION_PLAN.md`）

```mermaid
flowchart LR
    B[浏览器（管理员）] -->|/api/*| C[caddy]
    C --> A[api]
    subgraph oc1[OpenClaw 服务器（内网模式）]
        O1[OpenClaw + openclaw-channel]
    end
    subgraph oc2[OpenClaw 服务器（外网模式）]
        O2[OpenClaw + openclaw-channel] --> T[本机隧道 ghostunnel]
    end
    O1 -->|私网 HTTPS| G[agent-gateway 内网入口]
    T -->|公网 HTTPS + 双向证书| G2[agent-gateway 外网入口]
    G -->|/agent-api /mcp /im/api /im/obj| A
    G2 -->|/agent-api /mcp /im/api /im/obj| A
    G -->|/im/ws（API forward_auth 之后）| M[openim-server]
    G2 -->|/im/ws| M
    A -->|REST（imAdmin）| M
    M -->|回调（im 网络，固定来源地址）| A
    A --> S[(Garage 对象存储)]
    K[worker] -->|对账、收件箱、审批卡片| M
```

- **工作台是唯一的权限来源**：实例经 MCP 调工作台工具，写操作一律先生成待审批记录，管理员批准（高危要二次认证）后才执行；
  OpenClaw 智能体只开工作台工具与 IM 发消息工具。
- **浏览器不直连 OpenIM**：管理员的 IM 全部经 API 代理；实例的 REST 请求经接入网关的白名单与参数校验（`app/domain/im_gateway.py`），
  WebSocket 透传到消息网关前先由 API 校验 IM 令牌。
- **消息投影 + 双保险**：OpenIM 的 after 回调写收件箱，Worker 处理后拉取消息写进 `im_messages`；30 秒一次的增量对账和每天的全量对账补上丢失的回调。
  before 回调同步把关（实例之间的单聊、群聊防刷屏、停用的实例），工作台不可用时 IM 消息直接发送失败。
- **密钥**：实例启动时由取密钥小程序经接入入口取用授权给它的密钥（OpenClaw 的 exec SecretRef），每次取用都有记录；
  密钥库的密文用可轮换的凭证加密密钥保存。
- **运维**：运维页显示 OpenIM 与各实例的状态；`/metrics` 输出实例、取密钥、MCP、回调积压、对账补齐、防刷屏、待审批的指标（`docs/deployment.md` 第 12.2 节）。
