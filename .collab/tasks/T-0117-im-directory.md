---
id: T-0117
title: 会话目录：im_list_targets / im_read_messages（能私聊的管理员、所在的群与规则状态、白名单状态）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0116
created: 2026-11-04
updated: 2026-11-04
---

# T-0117 会话目录：im_list_targets / im_read_messages（能私聊的管理员、所在的群与规则状态、白名单状态）

## 目标
让智能体知道能私聊谁、在哪些群、群的规则和当前状态，并能读自己会话的最近消息（方案第 3 节）。

## 验收标准
- [ ] `im_list_targets`：能私聊的管理员（含 `replies_reach_you`）、所在的群（成员、规则、暂停状态、这一分钟剩余条数）、限额
- [ ] `im_read_messages`：只读自己的会话（私聊、入群之后的群消息），分页、截断
- [ ] 契约测试、单元测试；后端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 19:02 UTC · claude · 开始任务
- 2026-11-04 19:11 UTC · claude · 迁移 0033（agent_instances.bundle_whitelist，签发接入包时存私聊白名单快照）；im_list_targets / im_read_messages（app/domain/agent_im.py）；tests/test_agent_im_tools.py 3 个用例通过。与方案的偏差：方案写 T-0117 不需要迁移，为了判断“他回你你收不收得到”加了 0033。
