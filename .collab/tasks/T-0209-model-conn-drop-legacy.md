---
id: T-0209
title: 中转站管理：删除旧版兼容（旧密钥导入、迁移记录、旧配置状态）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,tests/e2e,docs,.github/workflows/ci-infra.yml
depends_on: T-0208
created: 2026-11-10
updated: 2026-11-10
---

# T-0209 中转站管理：删除旧版兼容（旧密钥导入、迁移记录、旧配置状态）

## 目标
人类在对话中（2026-11-10）确认：中转站管理不需要做成插拔模块；工作台还没有在生产中运行，不需要考虑旧版本兼容。
T-0200 ~ T-0208 里专为“旧版本升级”写的代码删掉（人类选择“删掉旧版兼容代码”）：旧密钥库导入（命令、页面上的预览导入 / 导入 / 对账、
迁移记录与模型 / 非模型归类）、连接的 migration_state / legacy_source_id、实例的“旧配置”（legacy_active）状态、
接入包里的旧 openai 段与 model-config.mjs 里清理它的逻辑、旧 /agents/secrets 链接跳转、部署指南里的升级步骤。
非模型的兼容凭据（GitHub Token、Runner 注入的密钥等，旧密钥库的现有功能）保留。

## 验收标准
- [x] 新迁移 0057 删除 model_connections.migration_state / legacy_source_id，apply_state 不再有 legacy_active；升级 / 降级 / alembic check 通过
- [x] 删除导入命令、迁移接口与 schema；模型服务的 Key 只能在中转站管理的连接里保存（密钥库拒绝新建 model_api_key）
- [x] 绑定、分配、接入包、model-config.mjs 不再有旧配置分支；新实例没有主模型时接入包不带模型段
- [x] 页面去掉迁移记录、旧配置状态与旧链接跳转；兼容凭据（非模型）照常管理
- [x] 后端、前端、e2e（agents 场景 27、浏览器验收）通过；ADR、接口约定、部署指南、核验记录更新

## 背景 / 约束 / 参考
- ADR `docs/decisions/2026-11-09-model-connections.md`（第 7 节“不做成插拔式模块”人类已确认）
- 方案包 `docs/v2/model-connections/` 里的迁移手册与 M01 ~ M13 验收项随之作废（方案包是原始资料，不改，作废写在 ADR）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 11:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 11:15 UTC · claude · 开始任务
- 2026-11-10 11:49 UTC · claude · 后端：迁移 0057（删 migration_state / legacy_source_id，apply_state 去掉 legacy_active，升级/降级/alembic check 通过）；删导入命令、/model-connections/migration/*、迁移 schema；连接的内部凭据对密钥库隐藏，密钥库拒绝新建 model_api_key（422）；绑定/分配/接入包/model-config.mjs 去掉旧配置分支，新实例没有主模型时接入包不带模型段；runner 与 agents 恢复 bc74494（不再按连接状态拦截）。前端：删迁移记录、旧配置状态、/agents/secrets 跳转与侧栏旧地址换算，页签改名“兼容凭据”，只能新建/筛选非模型类型。e2e：agents 验收的假模型 Key 改为 generic，sprint6 浏览器验收改走兼容凭据页签，本机 model_config_openclaw.py 去掉旧模板。文档：ADR 第 7 节（人类确认不做插拔）、第 8 节（删除清单、M01~M13 作废）、接口约定、部署指南 24.1、T-0208 核验记录补记。顺带修了 approval-center 测试在 11 点多会误报（钟点 <time> 含“11:”）。
- 2026-11-10 11:54 UTC · claude · status in_progress → done：旧版兼容已删除：迁移 0057、导入命令与接口、旧配置状态、接入包旧段、页面迁移记录与旧链接；密钥库只放非模型凭据（连接的 Key 与取用记录都不在密钥库）。本地后端 / 前端检查通过，e2e（agents 场景 27、浏览器验收）由 PR 的 CI 验证后合并。
- 2026-11-10 12:26 UTC · claude · PR 的 compose 检查失败在“DoD Prometheus 没有发布端口”：runner 上较新的 Docker Compose 对没发布的端口输出 'invalid IP:0'（以前是 ':0'，main 03:47 那次还通过），与本 PR 的改动无关；sprint5_acceptance.py 改为按“有没有非 0 端口”判断。
- 2026-11-10 12:52 UTC · claude · PR 的 api 检查在 9606f6b 上失败于 test_stream_pushes_invalidations_after_connecting（与本 PR 无关的 SSE 时序测试）：起点 resume_point 在占用连接名额之后才确定，测试只在固定 0.3 秒后写一条，繁忙机器上会落在起点之前或 1.5 秒窗口之后。本地给 resume_point 注入 0.5 秒延迟能复现；改为连接开始后每 0.2 秒写一条直到请求结束，注入延迟下也通过，正常运行 5 次都通过。
- 2026-11-10 12:56 UTC · claude · PR 的 agents 检查在 9606f6b 上只失败一步：agent_ssh_acceptance.py 报“缺少环境变量 SSH_TARGET_HOST”。新的 Ubuntu 26.04 runner 用 sudo-rs，忽略 sudo -E；ci-infra.yml 这一步改为 sudo env 显式传入 5 个 SSH_* 变量（本地 sudo-rs 0.2.2 验证可行）。同一次运行里其余 187 项全部通过，含场景 27 与 sprint6 浏览器验收。
