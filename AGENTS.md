# Codex Agent Instructions — My Workspace

你正在实现 `DEVELOPMENT_SPEC.md` 所描述的混合型全能个人工作台。

## 工作规则
1. 每轮先阅读 `DEVELOPMENT_SPEC.md`、`TASKS.md`、相关现有源码与变更记录。若目录尚无源码，先做 Sprint 0。
2. 仅实施当前选定 Sprint 的下一个未完成任务，不要跨 Sprint 一次生成全部系统；先写短计划，再修改代码。
3. 禁止发明第三方 API、Webhook 格式和版本。必须核验运行实例或当前锁定 release 的官方 OpenAPI/文档；不可验证则实现明确的接口桩和 TODO，不标为完成。
4. 任何数据表结构变化必须提供 Alembic 迁移和测试；不要直接更改第三方产品数据库。
5. 后端模块化单体，使用 FastAPI/Pydantic 2/SQLAlchemy 2；前端 Vue 3/TS，组件复用，API 强类型。
6. Token、Secret、Cookie、Webhook payload 必须脱敏；不能提交明文密钥、真实用户信息或生产配置。
7. 外部 HTTP 必须有 timeout、错误归类、重试限制；写操作具有幂等和未知完成状态处理。
8. 所有数据读取和写入均做 workspace scope + role 验证；不能只在前端隐藏按钮。
9. 对任何 AI/自动化有副作用的操作强制工具策略校验；Critical 类操作需要显式审批。
10. 完成前运行相关 lint/typecheck/tests/build/migrations；不能运行的项目注明原因、命令和实际输出，不得虚构通过结果。

## 提交格式
每次响应按以下结构总结：
- 当前 Sprint / 已完成任务
- 变更文件
- 实现决策及偏差
- 实际执行的命令和结果
- 安全/数据迁移影响
- 遗留问题与下一步

## 最高优先级架构约束
- Vikunja 是任务主源；AFFiNE V1 仅深链接与收藏，不猜测内容读写 API。
- 工作台 Owns User/Workspace/Widget/Calendar/Mapping/Notification/Audit。
- 第三方服务故障可降级；Webhook Inbox + 周期对账双保险。
- 核心 Compose 优先，n8n/AFFiNE/Monitoring 可选部署，生产禁止浮动 latest 镜像。
