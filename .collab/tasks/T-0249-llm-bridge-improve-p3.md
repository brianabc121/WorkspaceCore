---
id: T-0249
title: 大模型客户端改进第 3 期：逐字输出与推送通道，等待与用量显示（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,infra/caddy,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0249 大模型客户端改进第 3 期：逐字输出与推送通道，等待与用量显示（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 的 3.2 和聊天页的 C1、C6、C8：回答逐字出现、断线能接上；等待时说明用了多久、在做什么、在等什么；回答下面有用时和 token。

## 验收标准
- [x] 先在锁定版本上核验三家官方程序的增量输出（Claude Code、CodeBuddy Code 的 `--include-partial-messages`，Codex 的 `item/agentMessage/delta`）
- [x] 管理器：增量攒成事件（`delta`），回合结束后删掉；刷新页面拿得到已经写出来的部分
- [x] 工作台：按会话推送事件的接口（SSE），带编号接续；反向代理对它不压缩、不缓冲
- [x] 聊天页：先用推送、不行退回长轮询；逐字显示；已用时间、正在做什么、排队原因；回答下面的用时和 token
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 20:13 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 20:13 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 3 期）
- 2026-11-15 20:13 UTC · claude · 开始任务
- 2026-11-15 20:13 UTC · claude · 核验（按项目规则第 3 条）：claude 2.1.323 --help 有 --include-partial-messages；codebuddy 2.184.0 --help 有同名参数，类型定义 stream-json-protocol.d.ts 里 PartialAssistantMessage{type:'stream_event', event}、text_delta / content_block_delta；codex 0.169.0 生成的 ServerNotification.json 里有 item/agentMessage/delta（threadId / turnId / itemId / delta）。没有真实账号，实际输出没有跑过。越界：改了 infra/caddy/routes.caddy（新的推送路径不压缩、不缓冲）。
- 2026-11-15 20:13 UTC · claude · status in_progress → done：第 3 期完成
