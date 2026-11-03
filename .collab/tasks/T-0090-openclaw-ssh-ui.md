---
id: T-0090
title: OpenClaw 全自动部署 C：实例页“通过 SSH 自动部署”表单、指纹确认与安装进度
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web,tests/e2e,docs
depends_on: T-0089
created: 2026-11-03
updated: 2026-11-03
---

# T-0090 OpenClaw 全自动部署 C：实例页“通过 SSH 自动部署”表单、指纹确认与安装进度

## 目标
在 OpenClaw 实例页加“通过 SSH 自动部署”：填写服务器 IP、端口、账号，显示主机公钥指纹让 owner 核对确认，
输入一次性密码后开始安装并显示进度；安装完成后实例上线。后端接口来自 T-0089。
设计见 `docs/decisions/2026-11-03-openclaw-ssh-provisioning.md`。

## 验收标准
- [x] 表单：IP / 域名、端口、账号；探测后显示指纹与核对方法（`ssh-keygen -lf`），确认后才能输入密码
- [x] 密码只在提交安装时发送一次，页面不保存、不回显
- [x] 安装进度：每一步的状态、失败原因与脱敏日志；完成后显示实例在线
- [x] 重装、卸载、吊销专用密钥的入口（T-0089 B3 提供接口）
- [x] 单元测试、浏览器验收；部署教程更新；`apps/web` 的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
- 依赖 T-0089（后端）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 04:14 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 04:14 UTC · human · owner unassigned → claude：人类在对话中指派：SSH 自动部署全流程（见 T-0089 的指派记录）；本任务是 C 部分（网页），等 T-0089 的后端完成后开始
- 2026-11-03 08:02 UTC · claude · 开始任务
- 2026-11-03 08:33 UTC · claude · 实现：SshDeploy.vue（实例卡片“通过 SSH 自动部署”：填写服务器 → 指纹与核对命令 → 勾选后确认 → 一次性密码 → 每 2 秒轮询进度 → 重新部署 / 卸载 / 吊销 / 强制吊销 / 更换与删除服务器）、ssh.ts（查询、步骤名、错误说明）、auth.slowApi（读取主机公钥与吊销用 60 秒超时）。测试：agents-ssh.test.ts 6 个；tests/e2e/agent_ssh_browser.cjs 接进 ci-infra 的 agents job（越界改 .github/workflows/ci-infra.yml：CI 步骤只能放在那里）。本机真实 API + 真实 sshd 跑通到插件一步（沙箱代理拦截 npm），卸载、吊销、删除通过。越界改 services/api/app/domain/agent_ssh.py 一行：第一次部署没填密码的错误码 VALIDATION_ERROR → VALIDATION_FAILED（T-0089 是我的、已完成；全项目统一），测试补断言。教程第 8.6 节加「③+ 通过 SSH 自动部署」，ADR 第 7 节。
- 2026-11-03 08:42 UTC · claude · status in_progress → done：网页 SSH 自动部署完成；随 PR 合并生效
- 2026-11-03 08:47 UTC · claude · Codex P2（PR #136）：先为服务器 A 输入密码再更换为 B，密码框保留旧值，确认 B 后可能把 A 的密码发给 B。修复：目标身份（地址、端口、账号、指纹）变化时清空密码，更换 / 删除服务器时也清空；新增测试（去掉修复时失败）。web 371 passed、lint、typecheck、build 通过
