---
id: T-0148
title: 多实例 M5 验收：CI 里同一台机器上两个真实 OpenClaw（SSH 部署）、各自负载、卸载互不影响、浏览器验收
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github,docs
depends_on: T-0147
created: 2026-11-07
updated: 2026-11-07
---

# T-0148 多实例 M5 验收：CI 里同一台机器上两个真实 OpenClaw（SSH 部署）、各自负载、卸载互不影响、浏览器验收

## 目标
在 CI 里用真实 OpenClaw 验收多实例（方案第 13 节）。

## 验收标准
- [ ] 同一台机器上经 SSH 部署两个真实 OpenClaw 实例，都上线、各自的负载都有数据
- [ ] 卸载其中一个，另一个不受影响；定时任务各自只有自己的一份
- [ ] 浏览器验收：服务器页、部署到服务器、每个智能体的负载、手机宽度
- [ ] 核验记录补上 CI 实测结果

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
