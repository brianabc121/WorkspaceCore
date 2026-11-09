---
id: T-0194
title: AI 审批与管理区（B4）：多邮箱配置与智能体分配
owner: claude
status: done
priority: P1
branch:
paths: apps/web/src/pages/MailSettingsPage.vue,apps/web/src/features/mail,apps/web/tests
depends_on: T-0189
created: 2026-11-09
updated: 2026-11-09
---

# T-0194 AI 审批与管理区（B4）：多邮箱配置与智能体分配

## 目标
邮件设置页按效果图 05：OAuth 客户端状态、多邮箱表、所选邮箱的授权 / 分配 / 署名。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B4）

## 验收标准
- [x] 回调地址来自 MailSettingsOut.redirect_uri（可复制），client_secret 只显示是否已设置，留空保持
- [x] can_read / can_send 按实际 scope；needs_reconnect 的账号不消失、给重新连接
- [x] 分配（PUT agents）与署名（PATCH）分别提交、各用最新 version；分配不打开实例发信；新邮箱默认不分配
- [x] 切换邮箱清掉前一个账号的草稿；新测试与 mail 回归通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 13:25 UTC · claude · 邮件设置页按效果图 05 改版：OAuth 客户端卡片（回调地址来自接口可复制、密钥只显示是否已设置、留空保持）、邮箱账户表（需要重新连接的照常列出并给重新连接）、所选邮箱（?account=）的授权 / 分配 / 署名；分配与署名各自保存、用最新 version，409 提示核对不重发，后台刷新不抹掉草稿、换邮箱清草稿。新增 tests/mail-settings-redesign.test.ts（10 例），mail-pages 只改结构选择器；lint / typecheck / 全量 vitest 68 文件 656 例通过；亮暗、1440/1280/768/390 截图无横向滚动。
- 2026-11-09 13:26 UTC · claude · status todo → done：邮件设置改版完成（多邮箱表、所选邮箱的授权 / 分配 / 署名分别保存，OAuth 回调来自接口）；mail-settings-redesign 10 个用例，前端全量 68 文件 656 测试；顺手删掉 style.css 里不再使用的旧邮件设置样式
