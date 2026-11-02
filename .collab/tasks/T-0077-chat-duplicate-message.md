---
id: T-0077
title: 聊天窗口刚发出的消息偶尔显示两次（发送中占位与发送结果的去重）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/im/ChatWindow.vue,apps/web/tests/im.test.ts
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0077 聊天窗口刚发出的消息偶尔显示两次（发送中占位与发送结果的去重）

## 目标
人类要求修复聊天窗口消息重复。PR #117 的 CI（ci-infra / agents 的 V2 浏览器验收）因此失败过一次：
同一条刚发出的消息在会话里出现两次。

原因：发送接口先写一条 `sending` 占位（`client_msg_id = myws-<Idempotency-Key>`，没有 `server_msg_id`）并推送事件，
浏览器可能在发送返回前就刷新列表拿到这条占位；发送返回的消息带 OpenIM 的 `server_msg_id` / `client_msg_id`，
前端放进 `pending`。两条的键不同，`chronological()` 去不了重，于是显示两次，直到下一次列表刷新（事件丢了就一直重复）。

## 验收标准
- [x] `ChatWindow.vue`：`pending` 记住每条消息发送用的 Idempotency-Key；拿到发送结果后隐藏列表里 `myws-<key>` 的占位；
      `server_msg_id` 或 `client_msg_id` 任一与服务端列表相同即视为已追上，移出 `pending`
- [x] 回归测试：发送后列表仍是 `sending` 占位时只显示一条（旧代码下该测试失败，修复后通过）
- [x] `apps/web` 的 lint / typecheck / test / build / check:api 通过；相关浏览器验收（`tests/e2e/sprint6_browser.cjs`）由 CI 的 agents 任务运行

## 背景 / 约束 / 参考
- 后端发送流程：`services/api/app/domain/im_service.py`（本地占位 → 发送 → 更新为 OpenIM 的 ID）；本任务不改后端
- `AssistantChat.vue` 只显示服务端列表、没有本地 `pending`，不受影响

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 13:58 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 13:58 UTC · claude · 开始任务
- 2026-11-02 13:58 UTC · claude · 人类在对话中要求修复聊天窗口消息重复（来源：PR #117 的 agents CI 失败）
- 2026-11-02 13:58 UTC · claude · 新测试在旧 ChatWindow.vue 上失败（第 4 条出现 2 次），修复后 im.test.ts 18 项通过；apps/web：lint、typecheck、vitest 33 个文件 329 项、build、check:api 全部通过。sprint6_browser.cjs 需要真实 OpenIM + OpenClaw，本地没跑，由 CI agents 任务验证
- 2026-11-02 13:58 UTC · claude · status in_progress → done：聊天窗口发送中占位与发送结果去重，消息不再重复显示
