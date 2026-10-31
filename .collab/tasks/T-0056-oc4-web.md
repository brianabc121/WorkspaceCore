---
id: T-0056
title: OC-4 管理员端页面：IM、通讯录与分组、OpenClaw 实例、密钥库、审批中心与活动记录、移动端（S6-02）
owner: claude
status: todo
priority: P1
branch:
paths: apps/web
depends_on: T-0055
created: 2026-10-31
updated: 2026-10-31
---

# T-0056 OC-4 管理员端页面：IM、通讯录与分组、OpenClaw 实例、密钥库、审批中心与活动记录、移动端（S6-02）

## 目标
实现管理员端页面（方案第 4.7 节）。

## 验收标准
- [ ] IM 主界面：会话列表（置顶、免打扰、未读、分组筛选）、聊天窗口（置顶消息、@ 成员、图片、审批卡片）、会话详情（群成员、群设置、防刷屏设置）
- [ ] 通讯录与分组（没有删除好友）、OpenClaw 实例页、密钥库页、AI 审批中心与活动记录；顶栏未读与待审批数
- [ ] 390px 移动端无横向滚动；`apps/web` 的 lint、typecheck、test、build、`check:api` 通过；浏览器验收覆盖主流程

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
