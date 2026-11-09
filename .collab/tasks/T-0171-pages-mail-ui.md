---
id: T-0171
title: 四页改造（六）：邮件页默认全部邮箱 + 单邮箱三栏（账号绑定、回复身份、部分故障）与四页验收
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/mail,apps/web/src/pages/MailPage.vue,apps/web/tests,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0170
created: 2026-11-09
updated: 2026-11-09
---

# T-0171 四页改造（六）：邮件页默认全部邮箱 + 单邮箱三栏（账号绑定、回复身份、部分故障）与四页验收

## 目标
按效果图 04 / 05 改造邮件页：默认全部邮箱，单邮箱三栏；阅读、附件、回复都绑定所属邮箱；部分故障时照常显示其他邮箱；没有死按钮。

## 验收标准
- [x] 不带参数时总是全部邮箱；`?account=<id>` 打开单个邮箱；切换时保留搜索词，搜索与分类与当前范围一致
- [x] 每条邮件标明所属邮箱；阅读、附件、回复都带 account_id；回复身份是这封邮件所属的邮箱
- [x] 一个邮箱失效时提示并照常显示其他邮箱；未知的计数显示为空，不显示 0
- [x] 没有归档、删除、标记未读、转发等 Gmail 写操作按钮（只有 gmail.readonly + gmail.send）；“有附件”筛选因搜索语法无法核验暂不做
- [x] ≥1440px 三栏，更窄时阅读器为抽屉（焦点锁定、Esc 关闭），手机全屏并有“返回列表”
- [x] `mail-aggregate-pages.test.ts`、`mail-pages.test.ts`；前端检查通过
- [ ] 四页统一验收：浏览器脚本、四页亮暗与桌面 / 手机截图、两种邮件范围截图

## 背景 / 约束 / 参考
方案 `docs/v2/pages-redesign/`（规范 `docs/superpowers/specs/2026-11-08-workspace-four-pages-design.md`，计划 `docs/superpowers/plans/2026-11-08-workspace-four-pages-redesign.md`），效果图在 `assets/`。人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 01:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 02:29 UTC · claude · 开始任务：按四页改造方案实施（本分支）
