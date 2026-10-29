# My Workspace：混合型全能个人工作台开发实施规格

> 版本：1.0.0 | 文档日期：2026-10-30 | 状态：可供 Codex 分阶段实施 | 目标：单人优先、可扩展多用户、自托管

## 0. Codex 首次执行指令（必须遵守）

本文件是实现依据，不等于一次性完成命令。Codex 应先阅读根目录 `AGENTS.md` 与 `TASKS.md`，再执行 Sprint 0；每轮仅完成一个可测试的任务组，提交可运行代码、迁移、测试和文档。**禁止在没有运行和验证的情况下宣称已完成**。不得凭空假设第三方 API；使用锁定版本的官方 OpenAPI / 文档核对字段、认证、事件签名与分页。

首次运行交付：项目结构、开发环境、一键启动的 core Compose、healthz、数据库迁移、后端测试、前端基础框架、README；不部署所有集成服务。

## 1. 产品目标及范围

### 1.1 目标

建立自主可控的个人工作操作系统：统一首页、任务/项目、文档入口、日历、GitHub、运维、通知、搜索、自动化、AI 辅助。高频工作在本系统内原生操作，低频复杂编辑进入专业系统，所有系统由 Connector/BFF 集成。

### 1.2 技术边界

- 任务数据唯一主源：Vikunja；工作台不复制业务任务为主表，只保留外部对象映射、只读投影和本地 UI 状态。
- 文档主源：AFFiNE；V1 仅做跳转、收藏、人工添加文档元数据；**不能假设可通过未核验的 API 遍历、读取和修改 AFFiNE 全部文档**。
- 日程：V1 工作台本地日程为主源；Vikunja 截止日期作为只读派生事件；外部 CalDAV 同步推迟到 V1.1。
- GitHub：V1 仅只读仓库、Issue、PR、Actions 汇总；写权限后期审批开放。
- 监控：V1 只读指标和告警；不能在监控面板直接执行 Shell 或 Docker 管理操作。
- n8n：V1 列表、执行状态及受控 Webhook；不在工作台复制完整流程设计器。
- AI：先只读工具，再开发显式审批的写入操作；不得向 LLM 暴露密钥。

### 1.3 不在 V1 范围

聊天/IM、协同实时文档编辑、自建完整笔记编辑器、原生手机应用、替代 Grafana 查询语言、Kubernetes、微服务拆分、多租户计费、直接修改第三方系统数据库、自动远程执行系统命令。

### 1.4 验收总体标准

全新机器可按 README 启动核心服务；初始管理员可登录；首页可编辑布局；可以连接 Vikunja 并完成任务创建/编辑/查找/同步；第三方服务不可用时首页降级且无数据丢失；备份恢复可用；接口、权限、迁移、同步及前端关键路径有自动测试。

## 2. 技术栈与版本策略

| 领域 | 选择 | 原则 |
|---|---|---|
| 前端 | Vue 3 + TypeScript + Vite | Vue Router、Pinia、TanStack Vue Query |
| UI | Tailwind CSS + shadcn-vue | CSS 变量主题；避免直接硬编码颜色 |
| 表单/校验 | vee-validate + Zod | 表单 schema 和 API schema 对齐 |
| 拖拽布局 | GridStack 或维护活跃的 Vue 网格库 | Sprint 2 评估后固定 |
| 后端 | Python 3.12+ / FastAPI / Pydantic 2 | asyncio、httpx、可测试依赖注入 |
| ORM | SQLAlchemy 2 + Alembic | asyncpg；所有变更走迁移 |
| 数据库 | PostgreSQL 16+ | JSONB、GIN、pg_trgm；中文搜索有专门策略 |
| 缓存 | Redis 7+ | Cache、分布式锁；不要当唯一持久化队列 |
| 后台任务 | ARQ（初期） | Worker、定时同步；关键作业状态先入 PostgreSQL |
| 身份 | V1 单用户本地账号 + HttpOnly Session | 身份抽象 OIDC-ready；V1.1 Authentik |
| 反向代理 | Caddy | HTTPS，开发环境可不启用 TLS |
| 指标 | Prometheus + Grafana | 按 profile 可选启动 |
| 自动化 | n8n 独立部署 | Webhook 和受控执行 |
| AI | 模型兼容层 + 工具注册 | 接口抽象，不绑定单一模型厂商 |
| CI | GitHub Actions | lint / typecheck / tests / migrations / build |

版本要求：第三方产品固定**具体 release/tag 或 digest**，禁止生产默认使用 `latest`/`stable` 浮动标签。每个 Connector 保存 `tested_version`；升级先执行契约测试。以上技术栈是设计选型，不代表所有第三方已实际兼容验证。

## 3. 仓库与模块结构

```text
my-workspace/
├── AGENTS.md
├── TASKS.md
├── README.md
├── .env.example
├── .gitignore
├── compose.yaml
├── compose.monitoring.yaml
├── Makefile
├── docs/
│   ├── architecture.md
│   ├── api-contract.md
│   ├── data-dictionary.md
│   ├── deployment.md
│   ├── threat-model.md
│   └── decisions/
├── apps/
│   └── web/
│       ├── src/app/                   # boot/router/providers
│       ├── src/pages/                 # 路由页面
│       ├── src/components/            # 共享 UI
│       ├── src/widgets/               # Widget Registry/Renderer
│       ├── src/features/              # dashboard/tasks/search...
│       ├── src/services/              # typed API client
│       ├── src/stores/
│       └── tests/
├── services/
│   └── api/
│       ├── app/main.py
│       ├── app/core/                # config/auth/security/deps
│       ├── app/api/v1/              # FastAPI router
│       ├── app/domain/              # entities/services/policies
│       ├── app/integrations/        # connector contracts/providers
│       ├── app/repositories/        # SQLAlchemy access
│       ├── app/jobs/                # workers/reconciliation
│       ├── app/models/              # ORM
│       ├── app/schemas/             # Pydantic request/response
│       ├── migrations/              # Alembic
│       └── tests/
├── infra/caddy/
├── infra/monitoring/
└── scripts/
```

前端组件禁止自行持有第三方服务 Token；后端业务逻辑不能依赖 FastAPI 路由函数；SQL 访问集中在 repository；Connector 只处理外部协议与归一化。

## 4. 系统架构与数据流

```mermaid
flowchart TD
    Browser[Browser/PWA] --> Caddy[Caddy]
    Caddy --> Web[Vue SPA]
    Caddy --> API[FastAPI BFF]
    API --> Auth[Auth/Policy]
    API --> Domain[Dashboard/Task/Search/AI]
    Domain --> PG[(PostgreSQL)]
    Domain --> Redis[(Redis)]
    Domain --> Connect[Connector Adapters]
    Connect --> Vikunja[Vikunja]
    Connect --> AFFiNE[AFFiNE - deep link only V1]
    Connect --> GitHub[GitHub]
    Connect --> Monitor[Prometheus/Grafana]
    Connect --> N8N[n8n]
    API --> Inbox[(Webhook Inbox)]
    Worker[ARQ Worker/Reconciler] --> Inbox
    Worker --> PG
    Worker --> Connect
    Worker --> Redis
```

所有页面 HTTP/REST 调用 BFF；实时事件使用 SSE `/api/v1/events/stream`，SSE 仅发轻量失效通知，客户端收到后重新拉取。外部 Webhook 先校验再落库，快速返回 2xx，异步任务做后续处理。

### 4.1 创建任务成功路径

1. 浏览器 `POST /api/v1/tasks`，附 `Idempotency-Key` UUID。
2. BFF 验证会话、CSRF、工作空间权限、请求体及外部连接状态。
3. 在本地记录幂等请求状态（不能先声称成功）。
4. Connector 调用目标 Vikunja API，超时需要保留 `unknown` 状态而不是盲目再创建。
5. 获得外部 ID 后写 external_resources/upsert 搜索投影；若本地写失败，记录待修复任务。
6. 返回统一 Task DTO；后续用 Webhook/轮询对账校正。

**分布式写入限制：** 不能假设第三方 API 和本地 PostgreSQL 参与一个 ACID 事务。对跨系统操作使用幂等、状态机、定时核查与补偿；对于请求超时且不确定是否成功的场景，应先查询匹配结果或人工复核，避免重复创建。

### 4.2 数据所有权

| 数据对象 | 主源 | 工作台存储内容 | 修改方式 |
|---|---|---|---|
| 任务/项目 | Vikunja | 外部 ID、元数据投影、索引 | Vikunja Connector |
| AFFiNE 文档 | AFFiNE | 收藏 URL、手工标题、授权可得的元数据 | 跳转原系统 |
| Widget 与首页 | Workspace | 全量 | 本地 API |
| 本地日历 | Workspace | 全量 | 本地 API |
| GitHub 代码/Issue | GitHub | 状态投影 | V1 只读 |
| 监控时间序列 | Prometheus | 当前指标缓存 | 只读 |
| 自动化定义 | n8n | ID/运行状态/关联 | n8n 接口 |
| AI 对话和审批 | Workspace | 全量+留存策略 | 本地 API |

## 5. 数据库详细模型

所有自有表放入 `workspace` schema，主键 UUID，时间 `TIMESTAMPTZ UTC`，每张业务表含必要的 created_at/updated_at，敏感字段不直接存日志。所有含工作空间的查询必须限定 `workspace_id`，不得依赖前端过滤。

### 5.1 实体清单

1. `users(id,email,password_hash,display_name,status,created_at,updated_at)`：V1 本地用户，email 唯一。
2. `auth_sessions(id,user_id,token_hash,expires_at,last_seen_at,revoked_at,created_at)`：只存 session Token hash；Cookie HttpOnly Secure SameSite=Lax，跨站写入加 CSRF 防护。
3. `external_identities(id,user_id,issuer,subject,created_at)`：未来 OIDC 映射；unique issuer+subject。
4. `workspaces(id,name,owner_id,created_at,updated_at)`。
5. `workspace_members(workspace_id,user_id,role,created_at)`：role owner/admin/member/viewer，联合主键。
6. `integrations(id,workspace_id,provider,name,base_url,status,settings_json,created_at,updated_at)`：同工作空间 provider+name 唯一；base_url 要做 SSRF 防护。
7. `integration_accounts(id,integration_id,user_id,auth_type,credential_ref,scopes_json,expires_at,status)`：密钥从加密 Secrets/部署 Secret 提取，数据库只存密钥引用或密文。
8. `dashboards(id,workspace_id,owner_id,title,is_default,version,created_at,updated_at)`。
9. `widgets(id,dashboard_id,widget_type,config_json,layout_json,version,created_at,updated_at)`：必须校验 widget type 对应 schema。
10. `external_resources(id,workspace_id,integration_id,resource_type,external_id,title,source_url,metadata_json,source_updated_at,synced_at,deleted_at)`：unique integration+resource_type+external_id。
11. `resource_links(id,workspace_id,source_resource_id,target_resource_id,relation,created_at)`：跨系统项目↔文档引用。
12. `search_documents(id,workspace_id,resource_id,title,body,search_vector,indexed_at,visibility_json)`：资源与索引分离；仅索引授权可访问的文本。
13. `calendar_events(id,workspace_id,owner_id,title,description,start_at,end_at,all_day,timezone,rrule,created_at,updated_at)`：V1 recurrence 可只读或不实现，避免半成品规则。
14. `notifications(id,workspace_id,user_id,type,title,body,payload_json,source_key,read_at,created_at)`。
15. `webhook_inbox(id,integration_id,event_key,event_type,payload_json,status,attempts,next_retry_at,received_at,processed_at,error_text)`。
16. `sync_cursors(id,integration_id,resource_type,cursor_value,last_success_at,last_error,updated_at)`。
17. `outbox_events(id,workspace_id,event_type,payload_json,status,attempts,next_retry_at,created_at)`。
18. `idempotency_requests(id,workspace_id,actor_id,idempotency_key,request_fingerprint,status,response_code,response_json,created_at,expires_at)`：unique actor+key。
19. `automation_runs(id,workspace_id,integration_id,provider_run_id,status,started_at,finished_at,metadata_json)`。
20. `ai_conversations(id,workspace_id,user_id,title,created_at,updated_at)`。
21. `ai_messages(id,conversation_id,role,content_json,token_usage_json,created_at)`。
22. `ai_tool_executions(id,conversation_id,actor_id,tool_name,risk_level,arguments_redacted_json,status,approved_at,executed_at,result_summary,error_text)`。
23. `audit_logs(id,workspace_id,actor_id,action,resource_type,resource_id,metadata_redacted_json,ip_hash,created_at)`：追加写，避免存原始敏感数据。

### 5.2 关键 SQL 迁移（示意，Codex 必须拆为 Alembic revision）

```sql
CREATE SCHEMA IF NOT EXISTS workspace;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE workspace.workspaces (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 name text NOT NULL,
 owner_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace.integrations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace.workspaces(id) ON DELETE CASCADE,
 provider text NOT NULL,
 name text NOT NULL,
 base_url text,
 status text NOT NULL DEFAULT 'disabled',
 settings_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id, provider, name)
);

CREATE TABLE workspace.external_resources (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace.workspaces(id) ON DELETE CASCADE,
 integration_id uuid NOT NULL REFERENCES workspace.integrations(id) ON DELETE CASCADE,
 resource_type text NOT NULL,
 external_id text NOT NULL,
 title text,
 source_url text,
 metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
 source_updated_at timestamptz,
 synced_at timestamptz NOT NULL DEFAULT now(),
 deleted_at timestamptz,
 UNIQUE(integration_id, resource_type, external_id)
);
CREATE INDEX ix_resources_workspace_type
 ON workspace.external_resources(workspace_id,resource_type)
 WHERE deleted_at IS NULL;

CREATE TABLE workspace.webhook_inbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 integration_id uuid NOT NULL REFERENCES workspace.integrations(id),
 event_key text NOT NULL,
 event_type text NOT NULL,
 payload_json jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending',
 attempts integer NOT NULL DEFAULT 0,
 next_retry_at timestamptz,
 received_at timestamptz NOT NULL DEFAULT now(),
 processed_at timestamptz,
 error_text text,
 UNIQUE(integration_id,event_key)
);
CREATE INDEX ix_webhook_pending ON workspace.webhook_inbox(next_retry_at)
 WHERE status IN ('pending','retry');
```

此 DDL 不是完整数据库初始化脚本；`users`、`workspaces` 外键顺序、全部关联表由 Alembic 按拓扑关系建立。切勿直接将片段当成最终生产迁移。

### 5.3 搜索设计

- MVP：PostgreSQL `tsvector` 用于英文/技术词；中文先支持标题 `ILIKE` + `pg_trgm` 并标注相关性限制。
- 正式中文全文检索需评估 PGroonga 或应用层中文分词后建立 token 索引；不宣称 PostgreSQL `simple` 能良好中文分词。
- 查询必须带 workspace 授权过滤和用户访问可见性；索引里绝不写入 API Token、私钥、敏感日志。
- 对外部资源必须保存 `source_url` 并验证跳转链接 scheme 为 HTTPS（内网开发可例外），禁止脚本 URL。

## 6. API 契约规范

前缀 `/api/v1`，Content-Type `application/json`。统一错误：

```json
{"error":{"code":"INTEGRATION_UNAVAILABLE","message":"外部服务暂不可用","request_id":"req_...","details":{}}}
```

列表统一返回 `items,next_cursor,total_estimate`；未知总数可以 `null`。第三方服务超时返回可重试错误，不能伪造空列表或已成功状态。资源 ID 可以使用本地 UUID 及 `{provider}:{resource_type}:{external_id}` 统一表现形式，但内部必须通过 mapping 转换，不允许冒号拼接直接用于 SQL。

### 6.1 API 清单

| 方法 | 路径 | 作用 | 权限 |
|---|---|---|---|
| GET | `/healthz` | 进程健康 | public |
| GET | `/readyz` | DB/Redis 就绪检查 | internal |
| POST | `/api/v1/auth/login` | 登录 | public + rate limit |
| POST | `/api/v1/auth/logout` | 注销 | authenticated |
| GET | `/api/v1/me` | 当前用户 | authenticated |
| GET | `/api/v1/workspaces` | 工作空间列表 | authenticated |
| GET | `/api/v1/dashboard/overview` | 汇总 | viewer |
| GET/POST | `/api/v1/dashboards` | 查询/创建仪表盘 | viewer/member |
| PATCH | `/api/v1/dashboards/{id}` | 修改信息/版本 | member |
| GET/POST | `/api/v1/dashboards/{id}/widgets` | 查询/新增组件 | viewer/member |
| PATCH/DELETE | `/api/v1/widgets/{id}` | 配置/删除 | member |
| GET | `/api/v1/projects` | 外部项目列表 | viewer |
| GET/POST | `/api/v1/tasks` | 查询/创建任务 | viewer/member |
| GET/PATCH/DELETE | `/api/v1/tasks/{id}` | 任务详情/更新/删除 | viewer/member (删除需确认) |
| GET | `/api/v1/knowledge/resources` | 文档链接/索引 | viewer |
| POST | `/api/v1/knowledge/bookmarks` | 手工收藏链接 | member |
| GET/POST | `/api/v1/calendar/events` | 日程 | viewer/member |
| PATCH/DELETE | `/api/v1/calendar/events/{id}` | 日程修改/删除 | member |
| GET | `/api/v1/search?q=...` | 跨资源搜索 | viewer |
| GET/POST | `/api/v1/integrations` | 列表/配置 | viewer/admin |
| POST | `/api/v1/integrations/{id}/test` | 连通性验证 | admin |
| POST | `/api/v1/integrations/{id}/sync` | 手动同步 | admin |
| POST | `/api/v1/webhooks/{provider}/{integration_id}` | 外部事件 | signature verified |
| GET | `/api/v1/developer/repos` | GitHub 仓库 | viewer |
| GET | `/api/v1/developer/issues` | Issue | viewer |
| GET | `/api/v1/operations/services` | 服务状态 | viewer |
| GET | `/api/v1/operations/metrics` | 预设安全指标 | viewer |
| GET | `/api/v1/automations` | n8n 流程/运行 | viewer |
| POST | `/api/v1/automations/{id}/trigger` | 触发允许流程 | member + policy |
| GET/POST | `/api/v1/ai/conversations` | AI 对话列表/创建 | member |
| POST | `/api/v1/ai/conversations/{id}/messages` | 发送消息 | member |
| POST | `/api/v1/ai/tool-executions/{id}/approve` | 工具审批 | owner/admin |
| GET/PATCH | `/api/v1/notifications` | 通知和已读状态 | authenticated |
| GET | `/api/v1/events/stream` | SSE | authenticated |
| GET | `/api/v1/audit-logs` | 审计日志 | owner/admin |

### 6.2 Dashboard 聚合 DTO

```json
{
  "summary": {"pending_tasks": 8, "active_projects": 3, "unread_notifications": 2},
  "modules": {
    "tasks": {"status": "ok", "as_of": "2026-10-30T00:00:00Z"},
    "github": {"status": "stale", "as_of": "2026-10-29T23:00:00Z", "retryable": true}
  },
  "today_tasks": [],
  "widgets": [],
  "generated_at": "2026-10-30T00:10:00Z"
}
```

聚合接口不应同步等待所有 Connector；优先读取已授权的本地投影/缓存；关键 API 设总超时上限。返回 `ok/stale/unavailable/unauthorized` 以使前端可降级。

### 6.3 创建任务 DTO

```json
{
  "project_id": "vikunja:project:42",
  "title": "完成工作台首页",
  "description": "实现 Dashboard Widget",
  "priority": 3,
  "due_at": "2026-11-05T18:00:00+08:00",
  "labels": ["frontend"]
}
```

写接口 `Idempotency-Key` 必填；服务端校验请求内容哈希，与同一个键的不同请求应返回 409。更新使用版本/ETag 进行乐观并发控制；外部系统不支持时明确采取上次覆盖语义或拒绝冲突。

### 6.4 速率与响应约束

- `/auth/login`：IP + 账户双重限流与失败延迟；错误消息不泄露账户存在性。
- 全局分页 `limit` 默认 30，最大 100；排序稳定，分页 cursor 有签名/校验。
- 所有外部 HTTP 请求都有超时、重试上限、指数退避、熔断状态与 request_id。
- `/operations/metrics` 只允许预定义 PromQL 模板，禁止任意客户端 PromQL 经后端直连管理网络。

## 7. Connector 设计

```python
from typing import Protocol, Any

class Connector(Protocol):
    provider: str
    async def health_check(self) -> dict[str, Any]: ...
    async def list_resources(self, resource_type: str, cursor: str | None = None) -> dict[str, Any]: ...
    async def get_resource(self, resource_type: str, external_id: str) -> dict[str, Any]: ...

class WritableConnector(Connector, Protocol):
    async def execute(self, action: str, params: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
```

组件化能力 `Readable / Searchable / Writable / WebhookCapable` 分层；不要求 AFFiNE 实现其不支持的能力。Connector 的 errors 统一分类 `AUTH_FAILED`, `RATE_LIMITED`, `TIMEOUT_UNKNOWN`, `UNAVAILABLE`, `NOT_SUPPORTED`, `CONFLICT`, `VALIDATION_FAILED`。

### 7.1 Vikunja

- 新集成使用 **API v2**；运行实例 `/api/v2/docs`、`/api/v2/openapi.json` 是实际 schema 基准。
- API 版本与实例版本写入 integration metadata；通过 OpenAPI 生成类型或手工适配，禁止臆测 endpoint。
- 任务写操作通过服务端认证进行；用户凭证加密。默认个人模式可先使用受限服务 Token，未来多用户映射时必须重新评估权限。
- Webhook 按官方文档验证签名；Webhook 失败可能没有重试，必须配合轮询对账；事件处理必须幂等。
- 任务 CRUD、项目列表、优先级与标签要定义统一 TaskDTO，字段映射和允许值以锁定版本 API 为准。

### 7.2 AFFiNE

- 作为独立应用运行。官方建议 Docker Compose，包含 PostgreSQL/Redis/文件存储，升级不能直接删除 `.env` 或数据卷。
- V1：配置 `docs` 的外部 URL；用户手工添加收藏、关联项目，工作台只保存必要的书签元数据；不抓取私有文档正文。
- 如果未来要全文索引或原生编辑，必须先做 Spike 验证：可用 API、授权范围、速率限制、许可证、版本稳定性。未通过则保留 Deep Link 模式。

### 7.3 GitHub

- 接口采用 GitHub REST API；可用 GitHub App 或细粒度 PAT，优先最小权限。
- 支持仓库列表、Issue、PR、Actions 最近运行和故障摘要；ETag 条件请求、rate-limit header 和分页处理。
- GitHub Webhook 若启用，应验证签名，避免重复创建通知。

### 7.4 Prometheus / Grafana

- Prometheus 只对后端内部网络开放；FastAPI 仅公开允许列表中的指标视图。
- V1 指标：请求次数、P50/P95/P99 延迟、5xx 率、服务 up/down、CPU/内存（有 exporter 时）。
- 详细请求日志目标保留 7 天，与长期聚合指标分开；在隐私设置中可禁用请求路径的敏感参数。
- Grafana 仍负责复杂查询和告警配置，本系统只展示可用摘要和外链。

### 7.5 n8n

- n8n 默认作为独立服务和 Webhook 接收端，工作台仅管理白名单工作流 ID、执行状态和受控触发。
- 不将 n8n 管理 Token 下发到浏览器；工作流运行记录持久化错误摘要但不保存完整第三方敏感 payload。
- 对外商业托管或让其他客户配置工作流前重新核验 n8n 许可证。

## 8. Web 界面规范

### 8.1 桌面布局

左侧 220~260px 导航，顶栏 56~64px（全局搜索、创建、通知、用户），中央响应式 12 列 Widget Grid，右侧 320~420px 可收起 AI/详情抽屉。小屏幕侧栏折叠；移动端展示单列小组件。

主要页面：`/`、`/tasks`、`/projects`、`/knowledge`、`/calendar`、`/developer`、`/operations`、`/automations`、`/ai`、`/settings`、`/settings/integrations`、`/settings/security`。

### 8.2 Widget 插件规范

```typescript
type WidgetDefinition<TConfig> = {
  type: string;
  title: string;
  defaultSize: { w: number; h: number };
  configSchema: unknown; // 使用 Zod 在注册时实现
  component: unknown;
  refreshPolicy: 'manual' | 'interval' | 'event';
  requiredCapability?: string;
};
```

内置：TodoSummary、TodayTasks、ProjectProgress、CalendarAgenda、GitHubActivity、ServiceHealth、ApiLatency、RecentDocs、QuickLinks、AIQuickAsk、Notifications。`layout` 包含 x/y/w/h 和 desktop/mobile breakpoint 配置；设置页能导入/导出 JSON（校验 schema）。

### 8.3 页面状态

每个视图必须具备 loading、empty、error、unauthorized、stale；所有写操作防重复提交并显示确认结果，失败可重试。使用 toast 反馈；高风险删除弹窗二次确认。支持亮暗主题、键盘 `Ctrl/Cmd+K` 搜索、可访问性标签及触摸操作。

### 8.4 原生任务中心

列表/看板两种视图；筛选状态、项目、截止日期、优先级；新增/编辑 Task Drawer；可选择跳到 Vikunja 原生界面完成高级操作。必须保证同一任务的外部来源清晰标识。

## 9. 鉴权与安全设计

### 9.1 V1 认证

初始管理员使用首次启动引导创建，不提供硬编码默认管理员密码。密码 Argon2id 哈希，Session Token 只存 hash，Cookie HttpOnly、Secure（HTTPS）、SameSite=Lax。所有改变状态的 Cookie Auth 接口启用 CSRF token + Origin/Referer 校验。登录失败限速、会话失效、注销和密码更改要撤销既有 session。V1.1 再采用 Authentik/OIDC（PKCE + state + nonce），应用保持独立的身份提供者抽象。

### 9.2 角色

`owner` 管理工作空间/密钥/AI 高危审批；`admin` 管理集成和成员；`member` 修改业务内容；`viewer` 仅查看授权内容。权限在服务端统一 Policy 层验证；每个列表查询和资源读取都验证归属，避免 IDOR。

### 9.3 关键风险

- SSRF：集成 Base URL 禁止 userinfo、危险 scheme、云 metadata 地址、非法 DNS 重绑定；内网白名单需管理员显式配置。
- 秘钥：加密存储/Secret Manager，`.env` 不提交 Git，日志/错误响应严格脱敏。
- Webhook：校验签名+时间戳（供应商支持时）+ body 大小限制；payload 不信任，入库后异步处理。
- AI Prompt Injection：来自文档、Issue、日志的文本都是不可信数据，不得改变系统权限指令；Tool Executor 强制校验策略。
- AI 高风险操作：必须用户逐次审批；展示工具、参数预览、目标资源、风险，审批短期有效并绑定参数哈希。
- Docker/Host：不挂载未隔离的 docker.sock 给工作台，不以 root 身份运行 API，不暴露 Redis/Postgres 公网端口。
- CSP/反向代理：Caddy 设置合理 CSP、HSTS（生产 HTTPS）、请求体限制、安全 header、可信代理头。

## 10. 异步同步、幂等与恢复

- Webhook Inbox 状态：`pending -> processing -> completed`；异常 `retry`，达到阈值 `dead_letter`。
- 出现相同 `event_key` 只记录一次；无可靠外部事件 ID 时使用明确的业务去重策略，不能简单认为时间戳必然唯一。
- Worker 从 PostgreSQL 拿任务时使用 `FOR UPDATE SKIP LOCKED` 或等效事务防重复消费；执行副作用本身仍要幂等。
- 重试时间指数退避加 jitter；永久认证失败停用自动重试并提示管理员。
- 同步游标仅在一批处理完毕且成功提交后前移；需要重叠时间窗口抵御时钟差和漏事件。
- 外部删除需要 tombstone 处理；周期对账覆盖 webhook 丢失、更新乱序和进程重启。
- SSE 断线可重连；不把 SSE 当唯一可靠事件队列。

## 11. Docker 与环境要求

### 11.1 Core Compose 最小服务

`web`、`api`、`worker`、`postgres`、`redis`、`caddy`。生产数据库使用强密码和独立持久卷。支持 `docker compose --profile monitoring ...` 启用可选服务；Vikunja/AFFiNE/n8n 各自单独 Compose 安装并通过受控网络连通。

### 11.2 环境变量示例

```dotenv
APP_ENV=development
PUBLIC_BASE_URL=http://localhost:8080
DATABASE_URL=postgresql+asyncpg://workspace:CHANGE_ME@postgres:5432/workspace
REDIS_URL=redis://redis:6379/0
SESSION_SECRET=GENERATE_STRONG_RANDOM_SECRET
CREDENTIAL_ENCRYPTION_KEY=GENERATE_32_BYTE_KEY
CORS_ALLOWED_ORIGINS=http://localhost:8080
LOG_LEVEL=INFO
```

生产环境应从 Secret 读取，不直接存储在 Compose 文件。`.env.example` 只放占位符。开启 `restart`、healthcheck、volume；数据库和 Redis 不映射到 `0.0.0.0`。

### 11.3 运行命令契约

`make setup` 创建示例配置、`make dev` 本地开发、`make up` 启动 core、`make migrate` 迁移、`make test` 跑测试、`make lint` 代码质量、`make backup` 备份、`make restore-verify` 隔离恢复验证、`make down` 安全停机。**禁止把删除 volumes 作为常规停机命令**。

### 11.4 备份

每日 PostgreSQL 逻辑备份 + 独立对象存储/配置备份；加密、异地保存、保留 7/30/90 日策略可配置；每月至少一次临时环境恢复演练。第三方 AFFiNE 的 Postgres、blob storage、config 必须一起备份，不能只备份主工作台数据库。

## 12. 测试规范

- 后端：pytest + pytest-asyncio + httpx ASGIClient；PostgreSQL 集成测试使用独立测试数据库。
- 前端：Vitest + Vue Testing Library；端到端 Playwright。
- Connector 契约：使用 mock HTTP 响应覆盖成功、分页、401、403、429、5xx、断网、超时未知、重复 webhook、丢事件与 schema 变化。
- 安全：CSRF、IDOR、SSRF、Token 泄露、权限提升、会话撤销、AI 工具审批绕过等必须有测试。
- 数据库：空库迁移升级、回退策略、既有数据迁移、唯一约束、异常事务测试。
- 性能：目标基础 API P95 <300ms（不含外部服务）、缓存命中 Dashboard P95 <500ms；这些是测试目标，不是承诺。

## 13. 分阶段交付计划（Codex 严格按顺序）

### Sprint 0：决策与骨架

**范围**：仓库、依赖锁定、Makefile、Compose、FastAPI+Vue 最小工程、CI、README、架构 ADR。**DoD**：`make up` 起服务，`/healthz` 200，Web 首页可打开，`make test` 与 `make lint` 通过。

### Sprint 1：用户与工作空间

**范围**：初始化管理员、Session/CSRF、角色、Workspace、Alembic、Settings 骨架、审计日志。**DoD**：不登录访问私有接口 401；普通用户不能跨工作空间访问；登出后 session 失效；迁移可从空库完成。

### Sprint 2：Dashboard / Widget

**范围**：Widget Registry、首页可配置网格、Widget 数据协议、布局存储、暗色主题、全局搜索 UI（占位/本地搜索）。**DoD**：拖拽后刷新布局不丢；未知 widget type 安全报错；部分 Widget 失败不影响整页。

### Sprint 3：Vikunja 首个真实 Connector

**范围**：受限凭证连接、OpenAPI 版本固定、项目/任务列表、创建/修改、幂等状态、Webhook Inbox、Worker、Reconciler。**DoD**：外部原生任务改动最终出现在工作台；重复提交不创建双份任务；模拟 webhook 丢失可经对账补齐。

### Sprint 4：知识入口、日历、搜索、GitHub

**范围**：AFFiNE 深链接及收藏，本地日历，跨资源搜索投影，GitHub 只读。**DoD**：无未授权文档正文抓取；搜索结果只显示可见资源；GitHub rate-limit 有清晰降级。

### Sprint 5：运维、通知、n8n

**范围**：服务状态、预设 Prometheus 指标、Grafana 外链、消息通知、SSE、n8n 白名单触发。**DoD**：不向浏览器暴露 Prometheus 管理端/Secret；失败明确显示 stale；触发自动化有审计记录。

### Sprint 6：AI、强化、安全发布

**范围**：聊天、只读工具、写工具审批、审计、备份恢复、观测、E2E、文档完善。**DoD**：无审批不能执行高危操作；攻击性文档内容无法绕过工具策略；能从备份恢复并通过 Smoke Tests。

每个 Sprint PR 必含：变更清单、数据库迁移与影响、API 变更、执行命令、测试结果、未完成项、回滚说明、必要的截图（涉及 UI 时）。

## 14. 任务拆分与代码提交规范

- 一个 PR 只解决一个清晰目标；不能为了进度而无关重构。
- 每一项任务先产出最小设计、测试，再实现、再运行自检。
- API Pydantic Schema 先行；前端生成或维护强类型接口，不复制字段约定。
- 新增服务必须有就绪检查、日志、timeout、明确错误状态与 README 配置。
- CI 最低门槛：backend ruff、mypy/pyright（可渐进）、pytest；frontend eslint、vue-tsc、vitest、build；compose config；迁移 smoke。
- 提交信息建议 `feat(api): ...`、`fix(sync): ...`、`docs: ...`；禁止提交 Secrets、测试数据库、`node_modules`、虚拟环境和用户个人数据。

## 15. 关键验收场景（必须自动化）

| ID | 场景 | 预期结果 |
|---|---|---|
| A01 | 首次部署空数据库 | 创建管理员与工作空间；迁移无错误 |
| A02 | 未登录调用任务 API | 返回 401，不泄露内容 |
| A03 | viewer 修改 Widget | 返回 403 |
| A04 | 保存并刷新首页布局 | 布局一致，版本号更新 |
| A05 | 第三方服务断开 | 首页显示 stale/unavailable，其余模块可用 |
| A06 | 创建任务重放同一幂等键 | 最多创建一个外部任务 |
| A07 | 相同幂等键不同请求体 | 返回 409 |
| A08 | Vikunja Webhook 重复送达 | 事件最多执行一次有效副作用 |
| A09 | Webhook 丢失 | 周期对账最终纠正投影 |
| A10 | 源对象删除 | 搜索投影标记删除，页面无僵尸引用 |
| A11 | 搜索另一工作空间数据 | 不返回未授权结果 |
| A12 | 使用危险 `http://169.254.169.254` 集成 URL | 被拒绝 |
| A13 | 失效的外部 Token | 显示重新连接，不无限重试 |
| A14 | AI 试图执行未经审批的删除 | Tool Executor 拒绝并记录 |
| A15 | 备份后在空环境恢复 | 数据/登录/首页配置通过 Smoke Test |
| A16 | 断开 SSE 后重连 | 页面能重新拉取最新投影 |
| A17 | GitHub 429 | 遵守限流和退避，不把空列表当正常 |
| A18 | 登录后 logout | 旧 Session 不能再访问私有 API |

## 16. 主要风险及决策记录

| 风险 | 影响 | 解决方法 |
|---|---|---|
| AFFiNE 全量公开 API 不确定 | 无法原生编辑或全面索引 | V1 Deep Link，Spike 验证后再扩展 |
| Vikunja API 版本升级 | 字段/认证/事件改变 | 固定版本、OpenAPI 与契约测试 |
| 外部 API 无分布式事务 | 数据部分成功 | 幂等、状态机、补偿、对账 |
| 复杂 SSO 多应用行为不同 | 登录体验不一致 | V1 本地会话；后期 OIDC 兼容性验证 |
| 监控与 Docker 管理权限 | 宿主机被接管风险 | 只读代理，禁止直通 socket |
| 跨系统中文搜索 | 中文相关性不佳 | 模糊搜索 MVP + 专用分词方案 |
| 资源占用失控 | 单机稳定性下降 | core 优先、可选模块、监控后扩展 |
| AI Prompt Injection | 工具滥用与泄露 | 数据不可信，独立 Policy + 审批 |

## 17. 参考资料与实现前核验清单

- Vikunja API v2：https://vikunja.io/docs/api-v2/ （以实例实际 `/api/v2/openapi.json` 为准）
- Vikunja GitHub：https://github.com/go-vikunja/vikunja
- AFFiNE 自托管：https://docs.affine.pro/self-host-affine/
- AFFiNE 官方 Compose：https://docs.affine.pro/self-host-affine/references/docker-compose-yml
- AFFiNE 配置：https://docs.affine.pro/self-host-affine/install/configuration
- AFFiNE GitHub：https://github.com/toeverything/AFFiNE
- n8n GitHub：https://github.com/n8n-io/n8n
- Authentik：https://docs.goauthentik.io/
- GitHub REST：https://docs.github.com/en/rest
- FastAPI：https://fastapi.tiangolo.com/

Codex 必须在集成前核实：实际 API 版本、许可证、认证范围、Webhook 签名结构、部署镜像标签、默认配置是否安全；核实结果写入 `docs/decisions/` 并提供来源链接。特别注意 AFFiNE 官方 Compose 模板可能使用仅限私有网络的数据库 trust 认证：**不能把该模板默认网络安全假设直接复制进与其他不可信容器共享的网络**。

## 18. 交付完成定义（Definition of Done）

只有同时满足以下条件，才可报告对应 Sprint 完成：代码已提交到版本控制、所有必需依赖固定版本、开发和部署命令可复现、迁移可执行、自动测试通过、手工 Smoke Test 通过、关键安全测试完成、README/配置示例更新、未实现能力明确标注、无明文密钥、无未经验证的第三方 API 假设。Codex 输出最终报告要逐条列出通过/失败/未运行及原因。
