---
id: T-0244
title: 大模型客户端：聊天输入框支持粘贴和拖拽图片、文件（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: apps/web/src/modules/llm_bridge,apps/web/tests,tests/e2e,docs/deployment.md
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0244 大模型客户端：聊天输入框支持粘贴和拖拽图片、文件（V2）

## 目标
聊天页原来只能点按钮选附件。人类要求像 Claude 的输入框一样：直接粘贴（截图、复制的图片或文件）或把文件拖进来，就加到这条消息里。

## 验收标准
- [x] 在输入框里粘贴：剪贴板里有文件时当附件加上，不往输入框写内容；只有文字时照常粘贴
- [x] 把文件拖进输入区：有提示；松开后加上
- [x] 和点按钮选的规则一致：对话会话只收图片（别的文件说明原因），单个 20 MB、一条消息 8 个
- [x] 前端 lint / typecheck / test / build 通过；浏览器验收脚本补上粘贴和拖拽（由 CI 跑）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 17:36 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 17:36 UTC · human · owner unassigned → claude：人类在对话中指派：输入框要和 Claude 的一样，直接粘贴或拖拽就能发文件、图片
- 2026-11-15 17:36 UTC · claude · 开始任务
- 2026-11-15 17:40 UTC · claude · status in_progress → done：输入框支持粘贴和拖拽图片、文件；规则与点按钮选的一致
