---
id: T-0120
title: openclaw-channel 补丁（回复被拒收时放回系统事件）与工具收口（禁用 message 等）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0118
created: 2026-11-04
updated: 2026-11-06
---

# T-0120 openclaw-channel 补丁（回复被拒收时放回系统事件）与工具收口（禁用 message 等）

## 目标
自动回复被拒收时让智能体知道；主动发消息只剩 `im_send` 一个出口（方案第 5.2、6.6 节）。

## 验收标准
- [ ] openclaw-channel 0.3.3 补丁：`deliver` 失败时用 `enqueueSystemEvent` 把原因放回同一个会话；构建时校验原始 tarball
- [ ] 接入包禁用 `message`、`conversations_send`、`conversations_turn`、`conversations_list`；给智能体的说明
- [ ] 实例页提示已部署的实例重新签发接入包
- [ ] 检查通过；实测项记入核验记录

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 07:48 UTC · claude · 开始任务
- 2026-11-06 07:48 UTC · claude · 补丁：接入包 bin/channel-patch.mjs（核对 0.3.3 dist/inbound.js 原文件 sha256 5beb827d…，在 deliver 的 catch 里调 api.runtime.system.enqueueSystemEvent，原因表与工作台 REJECT_REASONS 一致；结果 sha256 36e2670f…；已打过只核对）；install-plugin.sh 装好或已装都打补丁，SSH 步骤显示是否这次补上；启动包装按补丁标记上报 0.3.3+myworkspace.1，实例输出 bundle_outdated、实例卡片提示旧部署重新签发。收口：tools.deny 加 message / conversations_send / conversations_turn / conversations_list；im_send 说明与 CHECKLIST 同步。e2e：场景 2 检查不再提供发消息工具、场景 3/10/11 改用 im_send（假模型支持 {n}），新增场景 20（5002 拒收记录、补发、系统事件进下一轮）。本地：在真实 0.3.3 文件上打补丁通过 node --check；V2 / V3 等 CI 的 agents 任务实测后补进核验记录。
