---
id: T-0176
title: 二批四页（五）：开发者总览（已同步范围统计、仓库表、开放 PR、失败运行、权限快照、同步状态）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/developer,apps/web/src/pages/DeveloperPage.vue,apps/web/src/app/router.ts,apps/web/tests,tests/e2e
depends_on: T-0175
created: 2026-11-09
updated: 2026-11-09
---

# T-0176 二批四页（五）：开发者总览（已同步范围统计、仓库表、开放 PR、失败运行、权限快照、同步状态）

## 目标
开发者页按效果图改成总览：统计卡、仓库表、开放 PR、失败运行、最近更新与逐仓库权限面板；保留原 Issues / PR / Actions 标签。
方案见 `docs/v2/pages-redesign-2/`（Task 5）。

## 验收标准
- [ ] 统计标题为“已同步范围”；null 显示“—”，partial 有不完整提示；“待审 PR”改为“开放 PR”
- [ ] PR 不显示 CI 状态（没有每个 PR 的 CI 关联）；有 requested_reviewers 才标“已请求审查”
- [ ] 权限面板四项逐行显示通过 / 未通过 / 未核验与核验时间；仓库 access=ok 但没有快照仍是未核验
- [ ] 限流显示“GitHub 限流中”、retry_at 与 as_of；unauthorized 清除旧数据并引导重新连接
- [ ] 选中行只改权限请求，不重拉总览；快速切换时旧结果不串
- [ ] 无合并、重跑、Token 输入或浏览器直连 GitHub；`developer-overview.test.ts` 与既有测试通过；亮 / 暗、桌面 / 手机截图

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
