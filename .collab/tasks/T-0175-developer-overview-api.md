---
id: T-0175
title: 二批四页（四）：开发者本地投影聚合 GET /developer/overview 与逐仓库权限核验快照
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/api/v1/developer.py,services/api/app/domain/developer.py,services/api/app/schemas/developer.py,services/api/app/repositories/github.py,services/api/app/domain/integrations.py,services/api/app/domain/github_permission_evidence.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0175 二批四页（四）：开发者本地投影聚合 GET /developer/overview 与逐仓库权限核验快照

## 目标
开发者页需要本地投影上的全量聚合（仓库、开放 Issue、开放 PR、失败运行、最近更新）和逐仓库的权限核验快照，GET 不访问 GitHub。
方案见 `docs/v2/pages-redesign-2/`（Task 4）。

## 验收标准
- [x] 新增 `GET /api/v1/developer/overview`：viewer 可读；`range` 24h / 7d、`limit` 1–100、`q` 1–100 字符（转义 LIKE）、`cursor` ≤512；无权仓库 404
- [x] summary 覆盖所选范围全部记录，不受分页影响；不能确定时为 null + partial，不拿子集冒充全量
- [x] 开放 Issue 排除 PR；开放 PR 排除 merged / closed；失败只算 completed 且 failure / timed_out，按 source_updated_at 归窗，同一 run 只计一次
- [x] 最近更新标明 time_basis（source_updated_at / synced_at），不编造活动动词
- [x] 新增 `GET /api/v1/developer/repos/{id}/permissions`：返回管理员测试连接时保存的快照；未执行的检查是“未核验”；Token / 仓库清单 / 启停变化后清除；unauthorized 不返回旧证据，限流保留旧时间
- [x] 快照不含 Token、Header、原始错误体；两个 GET 都不发外部 HTTP（测试断言）
- [x] `test_developer_overview.py`、`test_github_permission_evidence.py` 与既有 GitHub / 集成测试通过；OpenAPI 与 schema.d.ts 机械生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
- 2026-11-09 05:33 UTC · claude · 越界说明：为让只缺 Actions 权限的仓库不把 Issue/PR 数也变成未知，改了 app/domain/github_sync.py（投影记 denied_kinds）；Token 被拒时清除权限快照改了 app/domain/sync.py。两个文件都不在其他进行中任务的范围内。
- 2026-11-09 05:33 UTC · claude · status in_progress → done：开发者总览与权限快照接口；新测试与全量后端测试通过（修了导入时定 NOW 导致的限流用例时序问题）
