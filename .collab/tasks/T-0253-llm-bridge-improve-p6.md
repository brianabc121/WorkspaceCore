---
id: T-0253
title: 大模型客户端改进第 6 期：聊天页（Markdown、复制、重试、草稿、换模型、键盘）（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0253 大模型客户端改进第 6 期：聊天页（Markdown、复制、重试、草稿、换模型、键盘）（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 4.3 里除 C1、C6、C8（第 3 期已做）、C4、C12（待定）之外的聊天页改进。

## 验收标准
- [x] C2 回答按 Markdown 显示，模型写的 HTML 不执行；代码块和整条回答可以复制
- [x] C3 没做成的回合可以重试，最后一条回答可以重新生成
- [x] C5 草稿按会话保存在浏览器里
- [x] C7 回答到了而人不在页面时，标签页标题上标出来
- [x] C9（部分）列表“加载更多”；标题在发出第一条时就定下来
- [x] C10（部分）Claude / CodeBuddy 的会话可以换模型，从下一回合起
- [x] C11 Esc 停止；输入框空着时按 ↑ 放回上一条
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 21:46 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 21:46 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 6 期）
- 2026-11-15 21:46 UTC · claude · 开始任务
- 2026-11-15 21:46 UTC · claude · status in_progress → done：第 6 期完成（部分项只做了一部分，见方案第 9 节）
