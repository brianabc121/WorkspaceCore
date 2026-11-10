---
id: T-0210
title: 应用中心后端：管理投影、健康检测、精确操作记录、启停状态版本与同步结果、新闻配置写锁（方案包 AC-0/1/2）
owner: claude
status: done
priority: P1
branch: b6-model-connections
paths: services/api,docs
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0210 应用中心后端：管理投影、健康检测、精确操作记录、启停状态版本与同步结果、新闻配置写锁（方案包 AC-0/1/2）

## 目标
人类 2026-11-10 在对话中给出 codex 新做的应用中心方案包（已存入 `docs/v2/app-center/`），要求全面落实。
本任务是后端部分（方案包 AC-0/1/2）：在现有模块清单 / 注册表 / ModuleService 上补管理投影（能力声明、部署、健康缓存、同步结果、可做的操作）、
显式健康检测、按模块精确过滤的操作记录；启停加状态版本（state_version）与跨进程操作锁，钩子结果持久化为同步记录（成功 / 失败 / 未知 / 无需同步），
owner 可重试；新闻早报钩子返回真实结果，所有写 TrendRadar 配置文件的入口共用部署级写锁、写前读最新状态。
不改“缺部署也能启用”“停用保留数据”“owner 改 / admin 只读”等既有规则。

## 验收标准
- [x] 迁移 0058：workspace_modules 加 state_version（旧行 0）；新表 module_sync_attempts（workspace 复合约束、固定状态与原因类别）；升级 / 降级 / alembic check 通过
- [x] GET /modules/management、/modules/{key}/management：owner/admin 可读，汇总基于全量，GET 不发外部请求（测试证明零探测）
- [x] POST /modules/{key}/health-check：只探测清单声明的服务，停用 409、缺部署不出站、无探测器 not_applicable、限流 429
- [x] GET /modules/activity 与 /modules/{key}/activity：workspace + resource_type=module + resource_id 精确匹配、签名游标
- [x] PATCH /modules/{key} 支持 expected_state_version（旧客户端可省略）；跨进程操作锁（会话级 advisory lock），忙 409 MODULE_BUSY；相同目标不重复钩子 / 审计
- [x] 钩子结果持久化（succeeded / failed / unknown / not_required / unrecorded / superseded），失败不回滚开关；POST /modules/{key}/reconcile（owner）按最新目标重试
- [x] 新闻钩子返回 HookOutcome；设置保存、排除词、Worker 重算、部署命令与钩子共用部署级配置写锁，停用后迟到写入不会重新打开推送
- [x] 后端测试（含两个真实数据库连接的并发测试）、ruff / mypy / 全量 pytest 通过；OpenAPI 与 schema.d.ts 重新生成

## 背景 / 约束 / 参考
方案包：`docs/v2/app-center/`（设计、接口与一致性契约、前后端计划、验收矩阵、效果图）。方案包里的 AC-0 ~ AC-6 是局部编号：AC-0/1/2 → T-0210，AC-3/4/5 → T-0211，AC-6 → T-0212。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 13:52 UTC · claude · 开始任务
- 2026-11-10 14:58 UTC · claude · status in_progress → done：后端完成：迁移 0058、管理投影 / 检测 / 操作记录 / 重试接口、跨进程操作锁、同步记录、新闻配置写锁；后端新测试 20 个与回归通过，核验记录 docs/verification/T-0212-app-center.md
