---
id: T-0028
title: Sprint 4 后端（二）：GitHub 只读 Connector——仓库 / Issue / PR / Actions 投影、限流与分页、ETag 条件请求（S4-04 后端）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests,compose.yaml,.env.example,docs/deployment.md
depends_on: T-0027
created: 2026-10-30
updated: 2026-10-30
---

# T-0028 Sprint 4 后端（二）：GitHub 只读 Connector——仓库 / Issue / PR / Actions 投影、限流与分页、ETag 条件请求（S4-04 后端）

## 目标
按 T-0025 的核验结论与 T-0026 的契约实现 GitHub 只读 Connector：仓库、Issue、PR、Actions 最近运行进入本地投影，页面读投影；
遵守 GitHub 的限流、分页与条件请求，限流时清楚降级（Sprint 4 DoD“GitHub rate-limit 有清晰降级”）。

## 验收标准
- [ ] GitHub 集成：复用 Sprint 3 的 Integration、凭证加密与 SSRF 允许列表（默认允许 `api.github.com`，写进 `.env.example` 与部署文档）；测试连接逐项检查 T-0025 列出的读取能力（能否列出仓库、读取 Issue / PR / Actions 运行），能识别多余权限时给出提示
- [ ] Connector 只读：仓库 / Issue / PR / Actions 运行经分页读取，写入 `external_resources` 投影；使用 ETag 条件请求；记录 `x-ratelimit-*`，达到限额或遇到次级限流时按 `retry-after` / 重置时间退避，不把空列表当正常（A17）
- [ ] Worker 周期同步（复用 Sprint 3 的同步锁与 `sync_cursors`）；源对象删除或不再可见时写 tombstone；Token 失效时集成转为 unauthorized，不无限重试（A13）
- [ ] `/api/v1/developer/*` 端点读投影，限定工作空间并校验角色；首页 `github` 模块与 GitHubActivity 组件按投影新鲜度显示 ok / stale / unauthorized / unavailable
- [ ] Connector 契约测试：mock HTTP 覆盖成功、分页、304、401、403（含次级限流）、429、5xx、断网与 schema 变化；另有针对真实 GitHub 的可选测试（需要 `TEST_GITHUB_TOKEN`，没有时跳过并注明）
- [ ] 日志与审计不含 Token；`make lint`、`make test`、`make openapi` 通过；新增依赖写入 backend-stack ADR

## 背景 / 约束 / 参考
- 依赖：T-0027 合并后开始（共用迁移链）；以 T-0025 的核验 ADR 为准
- 规范：第 6.4 节（外部 HTTP）、第 7 节与 7.3、第 10 节；验收场景 A13、A17
- 通用约束：AGENTS.md 第 8 节第 3、6、7、8 条（核验第三方 API、脱敏、外部 HTTP、workspace scope）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
