# WorkspaceCore：中转站管理与 OpenClaw 模型连接改造交付包

日期：2026-11-09（Asia/Shanghai）  
交付对象：Claude，负责落地开发、测试与交付。  
代码基线：origin/main @ 4fbf8637a59a06c78acc3fececfe26b26ad398dc（PR #180 已合并）。  
本包是开发设计与实施计划，不是已开发的功能，也不包含真实 API Key。

## 直接交给 Claude 的执行说明

请从最新 origin/main 开始，先阅读 AGENTS.md / docs/AGENTS.md、.collab 任务与交接及本包。核对是否已有实现或同路径任务，避免重复开工；保护未提交工作。按仓库协作流程建立正式任务，以下 MC-* 只是本包的局部工作编号，不是现有 T-* 编号。不要直接复制或改写已有任务/交接的 owner。

目标：新增“中转站管理”，集中管理官方与第三方模型连接；将旧密钥库中的模型 API Key 纳入新页面并保留授权；OpenClaw 以“连接 + 模型”配置模型服务。现有容器的非模型凭据不能因改造而丢失。

按顺序阅读：
1. docs/superpowers/specs/2026-11-09-model-connections-design.md：产品、页面、架构与范围。
2. docs/superpowers/specs/2026-11-09-model-connections-contract.md：建议的新接口、状态和一致性规则。
3. reference/code-baseline.md：实际代码接入点及已知事实。
4. docs/superpowers/plans/ 下的三个实施计划：后端、OpenClaw、前端。
5. docs/rollout/2026-11-09-model-connections-migration.md：可恢复迁移、切换与回退。
6. docs/acceptance/2026-11-09-model-connections-tests.md：验收与真实证据模板。
7. [视觉效果图](assets/01-model-connections.png)：所有数据均为示例。\n8. REVIEW.md：独立文档审阅发现与已落实修订。

执行顺序：MC-0 核验 → MC-1 连接与凭据 → MC-2 模型/检测 → MC-3 迁移兼容 → MC-4 智能体配置 → MC-5 运行时应用 → MC-6 管理页 → MC-7 分配入口 → MC-8 总验收。
三个计划可以分 PR 实施，但契约、数据迁移和运行时的前置依赖必须满足；有重叠 paths 的任务串行。

## 重要约定

- 本包替代上一包 workspace-ai-admin-redesign 中密钥库改造 B5 的产品方案；其余七页方案继续有效。不要再平行实现两个密钥库主入口。
- “迁移”优先迁移管理入口和模型连接元数据。保留已加密的 AgentSecret 作为内部凭据存储，防止复制密文到错误加密上下文或破坏运行容器引用。
- 主导航换成“中转站管理”；旧 /agents/secrets 链接跳转。非模型 Token 保留在同页的“迁移与兼容凭据”入口，不能强塞进模型协议或直接删除。
- 官方直连与中转都走同一连接模型；URL、协议、模型 ID 独立配置，不按模型名称猜测协议。
- 密钥不回显；OpenClaw 对应适配器取用授权连接的密钥。浏览器不能 GET 已存明文。
- 模型业务数据源改为新连接；exec SecretRef 可以作为受控取密钥技术保留。它不是给智能体开放 shell。
- 平台只管理连接配置、检测和下发；本期不建设统一推理转发网关、计费系统或 Token 配额系统。
- 不在本次顺带迁移 GitHub PAT、Gmail OAuth、服务器密码、CA、新闻模块独立存储的凭据。
- 效果图中的检测通过、已导入数量不是真实执行结果；初始系统必须展示实际状态。
- 修改数据库提供 Alembic 迁移；后端统一导出 OpenAPI，再生成前端 schema.d.ts，禁止手写生成类型。
- 新方案涉及既有 V2 D5/D13 表述的细化，请补充技术 ADR 与对应产品文档；不修改协作协议，不越过当前 Sprint 的人类批准边界。

## 本次交付验证

已 fetch 主干并读取关键源码；只生成本交付目录及压缩包，未修改产品源码、执行数据迁移或运行产品测试。上游 OpenClaw/模型服务协议的新增适配尚未实测，MC-0 必须完成核验并留下证据。
代码路径和 SHA256 清单见 reference/source-manifest.json；包内文件清单见 MANIFEST.json。
