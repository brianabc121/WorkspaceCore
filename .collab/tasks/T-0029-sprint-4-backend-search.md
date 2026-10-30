---
id: T-0029
title: Sprint 4 后端（三）：外部资源统一映射与搜索索引、全局搜索接口、可见性过滤与删除同步（S4-03、S4-05 后端）
owner: claude
status: todo
priority: P1
branch:
paths: services/api/app,services/api/migrations,services/api/tests
depends_on: T-0027, T-0028
created: 2026-10-30
updated: 2026-10-30
---

# T-0029 Sprint 4 后端（三）：外部资源统一映射与搜索索引、全局搜索接口、可见性过滤与删除同步（S4-03、S4-05 后端）

## 目标
实现跨资源搜索：把 Vikunja 任务 / 项目、GitHub 资源、AFFiNE 收藏、本地日历事件统一映射进搜索索引，提供全局搜索接口；
结果只包含当前用户在当前工作空间可见的资源，源对象删除后索引同步标记删除（规范 5.3、A10、A11）。

## 验收标准
- [ ] 迁移 `search_documents`（规范 5.1 第 12 项；空库 upgrade / downgrade 与 `alembic check`）；只索引授权可得的文本（标题、手工备注、已同步的元数据），绝不写入 Token、私钥与外部文档正文
- [ ] S4-03：资源写入或更新投影时同步更新索引（Vikunja 的 Webhook 与对账、GitHub 同步、收藏与日历的写操作）；源对象删除或写 tombstone 后索引标记删除，搜索不再返回（A10）；提供从投影重建索引的命令或作业
- [ ] 查询：英文 / 技术词用 `tsvector`，中文用标题 `ILIKE` + `pg_trgm`，在契约与文档里标注中文相关性的限制（规范 5.3，不宣称 `simple` 能良好分词）；稳定排序与 cursor 分页；限制 `q` 的长度
- [ ] S4-05：所有查询带 workspace 过滤与角色 / 可见性过滤（`visibility_json`）；跨工作空间、已删除、不可见的资源都不返回（A11），测试覆盖
- [ ] 结果里的 `source_url` 校验 scheme（HTTPS，内网开发例外；禁止脚本 URL）；`make lint`、`make test`、`make openapi` 通过

## 背景 / 约束 / 参考
- 依赖：T-0027、T-0028 合并后开始（需要它们的资源类型）
- 规范：第 5.1 节第 10、12 项、第 5.3 节、第 16 节（跨系统中文搜索的风险）；验收场景 A10、A11

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
