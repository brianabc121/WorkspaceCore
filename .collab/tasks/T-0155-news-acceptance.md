---
id: T-0155
title: 新闻早报 N4 验收：CI 真实 TrendRadar + 模拟数据源与模型、浏览器验收
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github/workflows,docs
depends_on: T-0152, T-0153, T-0154
created: 2026-11-07
updated: 2026-11-07
---

# T-0155 新闻早报 N4 验收：CI 真实 TrendRadar + 模拟数据源与模型、浏览器验收

## 目标
在 CI 里用真实的 TrendRadar 两个容器验收整条链路，数据源、大模型、推送用模拟服务；加浏览器验收；把实测结果补进核验记录。

## 验收标准
- [ ] 模拟 NewsNow / RSS / OpenAI 兼容模型 / 推送接收
- [ ] 到推送时间收到早报：模拟渠道收到、工作台入库、站内通知
- [ ] 智能体工具返回真实数据，不开放的 MCP 工具调不到
- [ ] 改关键词后下一次运行条目随之变化；反馈影响生成的配置
- [ ] 浏览器验收：设置页、早报页、首页组件、反馈、手机宽度
- [ ] 核验记录补 CI 实测

## 背景 / 约束 / 参考
人类 2026-11-07 在对话中给出设计文档《Workspace 接入 TrendRadar 升级方案》（https://claude.ai/artifact/KYLXzXTfMXh1QLJob9Teim），要求「按照这份设计方案进行全面落实」。落实方案见 docs/v2/NEWS_PLAN.md，核验见 docs/decisions/2026-11-07-trendradar-verification.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 16:07 UTC · claude · 创建任务（owner: claude）
