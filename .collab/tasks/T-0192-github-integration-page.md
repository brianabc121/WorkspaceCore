---
id: T-0192
title: AI 审批与管理区（B2）：GitHub 集成页与逐仓库权限核验
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/IntegrationsPage.vue,apps/web/src/features/integrations,apps/web/tests
depends_on: T-0189
created: 2026-11-09
updated: 2026-11-09
---

# T-0192 AI 审批与管理区（B2）：GitHub 集成页与逐仓库权限核验

## 目标
集成设置页按效果图 03 重排：连接与同步状态、逐仓库四项权限核验、修复引导、修改连接侧栏。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B2）

## 验收标准
- [ ] 四项权限只来自逐仓库实际核验结果（测试结果或 `GET developer/repos/{id}/permissions`），未核验显示“未核验”，不从 access=ok 推导
- [ ] rate_limited 显示 retry_at，unauthorized 提示重连并不展示旧私有数据；测试连接、刷新、手动同步分开
- [ ] Token 留空保留原值、不回显；最多 20 仓库、大小写去重、只收 github_pat_
- [ ] 新旧 GitHub 设置测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
