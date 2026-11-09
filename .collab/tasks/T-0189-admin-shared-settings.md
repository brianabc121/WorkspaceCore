---
id: T-0189
title: AI 审批与管理区（B1）：设置页与共享管理布局
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/components/management,apps/web/src/pages/SettingsPage.vue,apps/web/src/features/ProfileForm.vue,apps/web/src/features/WorkspaceForm.vue,apps/web/src/features/MembersPanel.vue,apps/web/src/app,apps/web/tests
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0189 AI 审批与管理区（B1）：设置页与共享管理布局

## 目标
把设置页改成效果图 02 的布局，并提供后面七页共用的管理页外壳（标题、说明、操作区、侧栏）。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 B1）

## 验收标准
- [ ] 共享 `ManagementPage` / `ManagementSection`（主干已有等价组件就复用），只管布局，不发请求
- [ ] 个人资料、工作空间各自保存；邮箱只读；本人角色显示；成员表与添加（已有账户或显式“同时创建账号”），不说成已发邀请
- [ ] owner 不可修改 / 移除，admin 不能改 admin；USER_NOT_FOUND、角色冲突、失权给具体反馈；dirty 不被刷新抹掉
- [ ] 快捷入口指向安全设置、侧栏与页面、应用中心、首页布局导入导出；角色说明
- [ ] 八页登记为宽页面，不改其他页面；members / auth 回归与新增行为测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 12:52 UTC · claude · 开始任务：人类在对话中要求按 codex 的 AI 审批与管理区方案全面落实
