---
id: T-0091
title: GitHub 接入改到工作台网页上配置（生成 Token 链接、读取可访问的仓库、保存后自动检查与同步），一键部署脚本不再配置 GitHub
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,scripts,tests/e2e,docs
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0091 GitHub 接入改到工作台网页上配置（生成 Token 链接、读取可访问的仓库、保存后自动检查与同步），一键部署脚本不再配置 GitHub

## 目标
人类要求“GitHub 的连接也改到在 workspace 平台上配置，而不是通过一键部署脚本进行配置”。
网页上原来只有 Token 与仓库列表两个输入框，引导都在 `deploy.sh connect` 里（预填权限的生成 Token 链接、
列出 Token 能访问的仓库供勾选、保存后检查权限并开始同步）。本任务把这些搬到「集成设置」页面，脚本不再询问 GitHub。

## 验收标准
- [ ] 接口：列出 Token 能访问的仓库（新 Token，或已保存的 Token），只给能管理集成的角色；Token 不保存、不写日志与审计
- [ ] 页面：预填只读权限、90 天有效期的“在 GitHub 生成 Token”链接与步骤说明；粘贴 Token 后读取仓库并勾选（最多 20 个）；读取失败时可以手动填写
- [ ] 保存后自动检查权限（显示每一项结果），通过后自动开始同步
- [ ] `deploy.sh connect` 不再询问 GitHub，只显示状态并指向「集成设置」；`app.commands.connect` 去掉 github / github-repos 子命令
- [ ] 后端与前端测试、浏览器验收；部署教程与手册更新
- [ ] ruff / mypy / pytest、`apps/web` 的 lint / typecheck / test / build / check:api、`collab.py check` 通过

## 背景 / 约束 / 参考
- GitHub 集成的核验与接口：`docs/decisions/2026-10-31-*github*`（T-0025 / T-0028）
- 原来的脚本流程：`scripts/deploy.sh` 的 `connect_github`（T-0076）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 04:14 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 04:14 UTC · human · owner unassigned → claude：人类在对话中指派：github 的连接也改到在 workspace 平台上配置，而不是通过一键部署脚本进行配置
- 2026-11-03 04:14 UTC · claude · 开始任务
- 2026-11-03 04:29 UTC · claude · 实现完成：POST /api/v1/integrations/github/repositories（新 Token 或已保存的 Token；不保存、不写审计）；集成设置的 GitHub 表单加三步说明与预填只读权限的生成 Token 链接、读取可访问的仓库并勾选（最多 20 个，可手动填写）、保存后自动检查权限（通过即同步）；deploy.sh connect 不再询问 GitHub，只显示状态与设置页地址；删除 connect github / github-repos 子命令与测试；教程、手册、接口清单、T-0076 ADR 已更新；本地截图检查桌面与 390px 暗色无横向滚动
