---
id: T-0151
title: 新闻早报 N0 核验：TrendRadar 6.11.0 / MCP 4.1.1（镜像 digest、配置、MCP 工具、存储、推送）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/v2/NEWS_PLAN.md
depends_on:
created: 2026-11-07
updated: 2026-11-07
---

# T-0151 新闻早报 N0 核验：TrendRadar 6.11.0 / MCP 4.1.1（镜像 digest、配置、MCP 工具、存储、推送）

## 目标
核验人类方案依赖的第三方 TrendRadar：锁定版本与镜像 digest，配置格式、MCP 工具与鉴权、数据存储、推送方式、对外访问，写成核验记录；据此把人类方案落到工作台（docs/v2/NEWS_PLAN.md）。

## 验收标准
- [x] 锁定 TrendRadar 6.11.0 / MCP 4.1.1，镜像按 digest 拉取成功
- [x] MCP 在运行实例上实测（传输、工具清单、返回格式、无鉴权）
- [x] 源码核验配置、存储、推送、对外访问，写进 docs/decisions/2026-11-07-trendradar-verification.md
- [x] docs/v2/NEWS_PLAN.md 写明与原方案的差异与原因、任务拆分

## 背景 / 约束 / 参考
人类 2026-11-07 在对话中给出设计文档《Workspace 接入 TrendRadar 升级方案》（https://claude.ai/artifact/KYLXzXTfMXh1QLJob9Teim），要求「按照这份设计方案进行全面落实」。落实方案见 docs/v2/NEWS_PLAN.md，核验见 docs/decisions/2026-11-07-trendradar-verification.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 16:07 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 16:08 UTC · claude · 开始任务
- 2026-11-07 16:08 UTC · claude · status in_progress → done：完成：锁定 6.11.0 / 4.1.1 digest；MCP 在运行实例上实测 27 个工具、Streamable HTTP、无鉴权；源码核验写进 docs/decisions/2026-11-07-trendradar-verification.md；落实方案 docs/v2/NEWS_PLAN.md
