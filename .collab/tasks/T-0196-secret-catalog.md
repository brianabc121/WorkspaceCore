---
id: T-0196
title: AI 审批与管理区（B6）：密钥库服务端搜索、元数据详情与取用记录
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/agents.py,services/api/app/api/v1/agents.py,services/api/app/schemas/agents.py,services/api/tests,services/api/openapi.json,apps/web/src/services/schema.d.ts,apps/web/src/pages/SecretsPage.vue,apps/web/src/features/agents,apps/web/tests,docs
depends_on: T-0189
created: 2026-11-09
updated: 2026-11-09
---

# T-0196 AI 审批与管理区（B6）：密钥库服务端搜索、元数据详情与取用记录

## 目标
密钥库按效果图 07：服务端搜索与类型过滤、全量匹配数、元数据详情、取用记录。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B6，契约第 4 节）

## 验收标准
- [ ] `GET /agents/secrets` 加 q / kind 与 total，先筛选后分页、LIKE 转义、cursor 绑定条件；`GET /agents/secrets/{id}` 只返回元数据
- [ ] 只有 owner；跨空间 404；任何响应都不含值 / 密文 / 历史值；grants 批量转换无 N+1
- [ ] 页面：表格 + 详情 + 取用记录（复用 secret_id 过滤）；轮换用当前 version；删选中项清面板；无眼睛 / 复制 / 假有效期
- [ ] 后端新测试、原密钥安全回归与前端新测试通过；OpenAPI 与 schema.d.ts 机械生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 12:52 UTC · claude · 开始任务：人类在对话中要求按 codex 的 AI 审批与管理区方案全面落实
- 2026-11-09 13:07 UTC · claude · 后端（B6）完成：GET /agents/secrets 加 q（名称/说明字面包含，ILIKE 转义 % _ \，拒绝 NUL）、kind，分页前过滤，AgentSecretPage 加 total（SQL COUNT，total_estimate 同值）；游标带 workspace+q+kind 摘要，换条件 422，旧版无摘要游标仅在无筛选时可用；新增 GET /agents/secrets/{secret_id}（owner、跨空间 404、只有元数据）；_secrets_out 原本就是一次 IN 查询批量取 grants，补了稳定排序。新测试 tests/test_secret_catalog_queries.py。下一步：前端 SecretsPage/详情/取用记录（计划 B6 Step 3–5）。
