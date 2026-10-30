# Codex 可执行任务清单

> 一次只取一个 Sprint；勾选完成前提供测试日志与验收结果。

## Sprint 0 — 工程骨架
- [x] S0-01 创建 Monorepo 目录结构、README、.env.example、.gitignore、Makefile。
- [x] S0-02 初始化 Vue3+TypeScript+Vite，路由、主题、基础布局及 Vitest。
- [x] S0-03 初始化 FastAPI/Pydantic2/SQLAlchemy2，`/healthz`、`/readyz`、pytest。
- [x] S0-04 建立 PostgreSQL/Redis/Core Compose，数据持久化及健康检查。
- [x] S0-05 集成 Alembic，空库迁移与初始化 smoke test。
- [x] S0-06 CI lint/typecheck/test/build/compose config，写运行手册。

## Sprint 1 — 用户、工作空间、安全
- [x] S1-01 User、Session、Workspace、Member 迁移及 repository。
- [x] S1-02 首次安装管理员初始化、Argon2id、session cookie、CSRF 与限流。
- [x] S1-03 Workspace 和 role policy，防 IDOR 用例。
- [x] S1-04 Settings 骨架、连接凭证密文或 secret 引用。
- [x] S1-05 Audit logs、会话撤销与 API 测试。

## Sprint 2 — Dashboard
- [x] S2-01 Dashboard/Widget 数据表及读写 API。
- [x] S2-02 Vue 顶栏、侧栏、响应式页面、dark/light theme。
- [x] S2-03 Widget registry/schema、基础卡片。
- [x] S2-04 Grid 拖拽/布局保存/版本冲突处理。
- [x] S2-05 聚合接口、loading/error/stale、E2E。

## Sprint 3 — Vikunja
- [ ] S3-01 核验锁定版本 OpenAPI、认证/字段映射并形成 ADR。
- [ ] S3-02 Integration、Token 授权及 Connector 基类。
- [ ] S3-03 Vikunja project/task list/create/update/detail API 与 UI。
- [ ] S3-04 Idempotency-Key 和未知执行状态处理。
- [ ] S3-05 Webhook 签名校验、Inbox、Worker、重试、死信。
- [ ] S3-06 周期对账、tombstones、同步 cursor、契约/E2E 测试。

## Sprint 4 — 文档与搜索
- [ ] S4-01 AFFiNE Deep Link、收藏和权限可见性，禁止未验证的私有 API。
- [ ] S4-02 本地日历 CRUD / agenda 展示。
- [ ] S4-03 外部资源统一映射与搜索索引。
- [ ] S4-04 GitHub 只读仓库/Issue/PR/Actions，限流/分页处理。
- [ ] S4-05 全局搜索 UI、跨工作空间安全测试。

## Sprint 5 — 监控和自动化
- [ ] S5-01 Prometheus/Grafana 可选 Compose、API 预设指标视图。
- [ ] S5-02 Service status、健康告警、降级状态。
- [ ] S5-03 通知中心、SSE 重连与缓存失效。
- [ ] S5-04 n8n 白名单触发及运行日志，密钥隔离。
- [ ] S5-05 监控保留 7 天策略及无敏感日志检查。

## Sprint 6 — AI 与发布
- [ ] S6-01 AI provider abstraction、conversations/messages。
- [ ] S6-02 只读工具与搜索检索，权限边界。
- [ ] S6-03 写工具审批、参数哈希、审计、反 Prompt Injection 测试。
- [ ] S6-04 备份、隔离恢复与演练脚本。
- [ ] S6-05 安全扫描、性能基准、完整端到端 Smoke Test。
- [ ] S6-06 更新部署文档、版本表、升级/回滚说明、发布 checklist。
