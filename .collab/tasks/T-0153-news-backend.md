---
id: T-0153
title: 新闻早报 N2 后端：迁移 0048、设置与密钥、配置生成、接收早报、MCP 客户端与 news_* 工具、反馈与偏好学习、方案 B
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs
depends_on: T-0151
created: 2026-11-07
updated: 2026-11-07
---

# T-0153 新闻早报 N2 后端：迁移 0048、设置与密钥、配置生成、接收早报、MCP 客户端与 news_* 工具、反馈与偏好学习、方案 B

## 目标
工作台后端：新闻早报的设置（含加密密钥）、按设置生成 TrendRadar 配置、接收推送入库、MCP 客户端与智能体只读工具 news_*、早报页面与首页接口、收藏与不感兴趣、偏好学习、方案 B（定时让智能体写早报）。

## 验收标准
- [ ] 迁移 0048（news_settings、news_briefings、news_feedback）与升级 / 降级测试
- [ ] 设置接口（owner）、密钥只写不读、全部署唯一启用；生成 config.yaml / frequency_words.txt / timeline.yaml / ai_interests.txt
- [ ] 接收接口：token、分批拼接、入库、站内通知
- [ ] MCP 客户端只调白名单工具；news_* 只读工具；页面接口（今天、往期、热榜、RSS、搜索、收藏）与首页模块
- [ ] 偏好学习调整生成的配置；方案 B 建任务、取 README、入库并推送
- [ ] 密钥纳入轮换；接口契约、数据字典、威胁模型更新；测试

## 背景 / 约束 / 参考
人类 2026-11-07 在对话中给出设计文档《Workspace 接入 TrendRadar 升级方案》（https://claude.ai/artifact/KYLXzXTfMXh1QLJob9Teim），要求「按照这份设计方案进行全面落实」。落实方案见 docs/v2/NEWS_PLAN.md，核验见 docs/decisions/2026-11-07-trendradar-verification.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 16:07 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 16:55 UTC · claude · 开始任务
- 2026-11-07 16:55 UTC · claude · 后端完成：迁移 0048（三张表 + agent_wakeups.source 增加 news）、设置与密钥（加密、只回已设置、全部署唯一启用）、配置文件生成、接收早报 /internal/news/ingest/<token>（分批拼接、通知 owner）、今天/往期/热榜/RSS/搜索/收藏接口、偏好学习、方案 B（任务 + 唤醒，完成后入库并经 MCP 推送、90 分钟超时）、news_* 只读工具、首页组件 news_briefing、运维页 TrendRadar、密钥轮换。测试：test_news_config 17、test_news_api 13、test_news_migration 1 通过；前端（T-0154）进行中
