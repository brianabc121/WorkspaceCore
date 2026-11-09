---
id: T-0193
title: AI 审批与管理区（B3）：修改密码与本人登录会话
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/SecurityPage.vue,apps/web/src/features/PasswordForm.vue,apps/web/src/features/SessionList.vue,apps/web/src/features/security,apps/web/tests
depends_on: T-0189
created: 2026-11-09
updated: 2026-11-09
---

# T-0193 AI 审批与管理区（B3）：修改密码与本人登录会话

## 目标
安全设置页按效果图 04：修改密码与本人登录会话分栏。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B3）

## 验收标准
- [ ] 新密码 12–128，确认新密码只在前端校验，请求仍是 current_password / new_password；尝试后清空
- [ ] 会话只显示 ID、创建、最近活动、到期、是否当前，不编造设备 / 位置 / IP
- [ ] 撤销其他会话只更新列表；撤销当前会话退出并清私有缓存；修改成功后其他会话已撤销的提示
- [ ] 新测试与 auth / sessions 回归通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
