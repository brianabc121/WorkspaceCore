---
id: T-0148
title: 多实例 M5 验收：CI 里同一台机器上两个真实 OpenClaw（SSH 部署）、各自负载、卸载互不影响、浏览器验收
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github,docs
depends_on: T-0147
created: 2026-11-07
updated: 2026-11-07
---

# T-0148 多实例 M5 验收：CI 里同一台机器上两个真实 OpenClaw（SSH 部署）、各自负载、卸载互不影响、浏览器验收

## 目标
在 CI 里用真实 OpenClaw 验收多实例（方案第 13 节）。

## 验收标准
- [x] 同一台机器上经 SSH 部署两个真实 OpenClaw 实例，都上线、各自的负载都有数据
- [x] 卸载其中一个，另一个不受影响；定时任务各自只有自己的一份
- [x] 浏览器验收：服务器页、部署到服务器、每个智能体的负载、手机宽度
- [x] 核验记录补上 CI 实测结果

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
- 2026-11-07 05:29 UTC · claude · 开始任务
- 2026-11-07 05:30 UTC · claude · agent_ssh_acceptance.py 改为一台服务器两个真实实例：添加→确认→准备（一次密码）→部署甲乙（不用密码）→各自网络命名空间与负载→甲的运维与服务器运维→重新部署甲、卸载甲时乙不受影响→乙还在时不能移除→卸载乙→吊销→移除；agent_ssh_browser.cjs 改为服务器页流程。只能在 CI 跑，结果看 PR。
- 2026-11-07 06:14 UTC · claude · CI agents（96a8d1c）两实例验收：准备一次、部署甲乙不用密码、各自命名空间与负载、运维、卸载甲乙不受影响、吊销移除都通过；发现部署过程中定时任务先上报建了一台自动识别的服务器、SSH 服务器接不过指纹（整机数据不记）。已修 host_metrics（接过指纹、并入并删除那台）与部署结束 / 手动指定时清理空的自动识别服务器，加单元测试；核验记录第 3 节写入 CI 实测。
- 2026-11-07 07:31 UTC · claude · status in_progress → done：完成：CI 同一台机器上 SSH 部署两个真实 OpenClaw，都上线、各自负载有数据；卸载一个不影响另一个、定时任务各自一份；服务器页浏览器验收；核验记录第 3 节补 CI 实测。PR brianabc121/WorkspaceCore#169
