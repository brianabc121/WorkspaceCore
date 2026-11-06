---
id: T-0141
title: 运行服务器 R6 验收：CI 里真实 Docker + gVisor + runner-agent、场景 26、浏览器验收
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github,docs,infra
depends_on: T-0139, T-0140
created: 2026-11-06
updated: 2026-11-06
---

# T-0141 运行服务器 R6 验收：CI 里真实 Docker + gVisor + runner-agent、场景 26、浏览器验收

## 目标
在 CI 里用真实 Docker + gVisor + runner-agent 验收运行服务器（方案第 15 节）。

## 验收标准
- [ ] CI 的 runner 上装 gVisor release-20261019.0，runner-agent 连工作台
- [ ] 场景 26：一次性测试跑完即删；会话按 Issue 关闭删除；常驻停止后唤起、归档后重建；gvisor 与 runc 都能跑
- [ ] 浏览器验收：运行服务器页面
- [ ] gVisor 实测结果补进核验记录

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
