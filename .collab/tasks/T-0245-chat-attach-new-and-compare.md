---
id: T-0245
title: 大模型客户端：新建会话的第一条消息和多模型同问可以带附件（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,tests/e2e,docs/deployment.md,docs/api-contract.md
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0245 大模型客户端：新建会话的第一条消息和多模型同问可以带附件（V2）

## 目标
T-0244 只改了会话里的输入框。新建会话页的第一条消息、“多模型同问”（新建一组、向一组继续发）还不能带附件。人类要求这两处也改好，按推荐的做法来。

## 验收标准
- [x] 新建会话：第一条消息可以点按钮选、粘贴、拖入附件；先建会话，再上传，再带着发第一条；没发成就带着原文和原因进会话
- [x] 多模型同问：新建一组和向一组继续发都可以带附件；附件分别传到每个成员的会话；有对话会话时只能带图片
- [x] 后端：建组可以 `send=false` 只建会话；向一组发送可以按成员带 `attachments`；有测试
- [x] 后端与前端的 lint / typecheck / test / build / check:api 通过；浏览器验收脚本补上同问带图一步（由 CI 跑）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 17:52 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 17:52 UTC · human · owner unassigned → claude：人类在对话中指派：新建会话的第一条消息、多模型同问的输入框也改好，按推荐的来
- 2026-11-15 17:52 UTC · claude · 开始任务
- 2026-11-15 18:01 UTC · claude · status in_progress → done：新建会话的第一条消息、多模型同问（新建和继续发）都可以带附件；后端建组可只建会话、发送可按成员带附件
