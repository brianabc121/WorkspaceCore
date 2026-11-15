---
id: T-0243
title: 大模型客户端：对话模式不能执行命令时让人知道（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,apps/web/src/modules/llm_bridge,apps/web/tests,docs/deployment.md
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0243 大模型客户端：对话模式不能执行命令时让人知道（V2）

## 目标
人类在 CodeBuddy 的对话会话里让它“看下当前环境”，模型把 `<tool_call>Bash_2(...)` 当成文字写了出来，反复几次都没有结果。
原因：对话模式启动官方程序时不给任何工具（`--tools ""`），但模型不知道，页面也没有说明。要让人和模型都知道对话模式不能执行命令、不能读写文件。

## 验收标准
- [x] Claude / CodeBuddy 的对话模式启动时追加系统提示：没有工具、不要假装调用、遇到这类请求直接说明并建议改用开发模式；Codex 的对话提示同样补上
- [x] 聊天页：对话会话的标题下标“不能执行命令”，会话信息的“用法”写明限制，新建会话时“对话”的说明写明限制
- [x] 对话会话里模型把工具调用写成文字时，那条回答下面说明它并没有执行；开发会话不标
- [x] 管理器与前端的 lint / typecheck / test / build 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 14:06 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 14:06 UTC · human · owner unassigned → claude：人类在对话中指派：对话模式不能执行代码时，需要让我们能够知道
- 2026-11-15 14:06 UTC · claude · 开始任务
- 2026-11-15 14:06 UTC · claude · status in_progress → done：对话模式明确告诉模型没有工具；聊天页标出不能执行命令，并标出模型只是写出来的工具调用
