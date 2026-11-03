---
id: T-0090
title: OpenClaw 全自动部署 C：实例页“通过 SSH 自动部署”表单、指纹确认与安装进度
owner: claude
status: todo
priority: P2
branch:
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
- [ ] 表单：IP / 域名、端口、账号；探测后显示指纹与核对方法（`ssh-keygen -lf`），确认后才能输入密码
- [ ] 密码只在提交安装时发送一次，页面不保存、不回显
- [ ] 安装进度：每一步的状态、失败原因与脱敏日志；完成后显示实例在线
- [ ] 重装、卸载、吊销专用密钥的入口（T-0089 B3 提供接口）
- [ ] 单元测试、浏览器验收；部署教程更新；`apps/web` 的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
- 依赖 T-0089（后端）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 04:14 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 04:14 UTC · human · owner unassigned → claude：人类在对话中指派：SSH 自动部署全流程（见 T-0089 的指派记录）；本任务是 C 部分（网页），等 T-0089 的后端完成后开始
