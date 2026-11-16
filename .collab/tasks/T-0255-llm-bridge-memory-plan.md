---
id: T-0255
title: 大模型客户端：上下文压缩与知识库结合的优化方案（只写方案）（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: docs/v2/LLM_BRIDGE_MEMORY_PLAN.md,docs/v2/README.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0255 大模型客户端：上下文压缩与知识库结合的优化方案（只写方案）（V2）

## 目标
人类要求：会话能自动压缩上下文以节省 token；压缩出的重点存进工作台知识库；与平台自带的压缩结合；提问前结合知识库。先看业内做法，写一份优化方案，不写代码。

## 验收标准
- [x] 在锁定版本上核对三个官方程序的压缩能力，标明没核实的
- [x] 查业内做法并给出来源，标明只有二手来源的
- [x] 方案写进 `docs/v2/LLM_BRIDGE_MEMORY_PLAN.md`，列出要人类决定的事

## 背景 / 约束 / 参考
- 与 `docs/v2/KNOWLEDGE_PLAN.md` 3.8 第 7 条（不自动把笔记塞进提示词）的关系：方案默认不改这条，自动模式列为待人类决定。
- 实施要等人类对方案第 9 节的答复，另开任务。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 05:51 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-16 05:51 UTC · human · owner unassigned → claude：人类在对话中指派：会话自动压缩上下文、与知识库结合、提问前检索知识库，先写优化方案并参考业内做法
- 2026-11-16 05:51 UTC · claude · 开始任务
- 2026-11-16 05:53 UTC · claude · status in_progress → done：方案写完：docs/v2/LLM_BRIDGE_MEMORY_PLAN.md；第 9 节 6 件事等人类决定，实施另开任务
- 2026-11-16 06:02 UTC · claude · 人类答复了方案第 9 节：会话纪要默认自动写入、知识库默认允许发给外部平台、新工具补进例外九名单，其余按推荐。已按此改方案（新增 5.3：默认开启后的保护；纪要先做不用模型的那一种）。
