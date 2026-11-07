---
id: T-0154
title: 新闻早报 N3 页面：早报页、首页面板、设置页、收藏与不感兴趣
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web
depends_on: T-0153
created: 2026-11-07
updated: 2026-11-07
---

# T-0154 新闻早报 N3 页面：早报页、首页面板、设置页、收藏与不感兴趣

## 目标
工作台页面：早报页（今天、往期、热榜、RSS、搜索、收藏）、首页“今日早报”组件、设置 → 新闻早报（全部设置、状态、测试推送、学到的偏好）、收藏与不感兴趣。

## 验收标准
- [x] 早报页与导航入口，Markdown 安全渲染，手机宽度可用
- [x] 首页“今日早报”组件
- [x] 设置页：兴趣主题、排除词、平台、RSS、筛选方式、大模型、推送渠道、推送时间、早报方式与智能体、状态与测试推送
- [x] 收藏 / 不感兴趣（可把词加进排除）
- [x] 前端测试；lint / typecheck / build / check:api

## 背景 / 约束 / 参考
人类 2026-11-07 在对话中给出设计文档《Workspace 接入 TrendRadar 升级方案》（https://claude.ai/artifact/KYLXzXTfMXh1QLJob9Teim），要求「按照这份设计方案进行全面落实」。落实方案见 docs/v2/NEWS_PLAN.md，核验见 docs/decisions/2026-11-07-trendradar-verification.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 16:07 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 17:46 UTC · claude · 开始任务
- 2026-11-07 17:46 UTC · claude · status in_progress → done：前端：早报页六个标签、设置页（时区只读）、首页“今日早报”组件、收藏 / 不感兴趣；lint、typecheck、vitest 467 个、build、check:api 通过
