---
id: T-0267
title: 建立稳定 PR 集成与分层 CI 协作规则并通知 Claude
owner: codex
status: in_progress
priority: P1
branch: codex/t0267-stable-pr-rules
paths: README.md,CLAUDE.md,docs/AGENTS.md,.collab/README.md,.collab/decisions,docs/superpowers/plans/2026-11-16-stable-pr-rules.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0267 建立稳定 PR 集成与分层 CI 协作规则并通知 Claude

## 目标
按人类本次明确要求，把“并行开发、固定 PR 提交、统一集成、分层验证”的规则写到首页 README，同步权威代理规则与 Claude 入口，消除旧规则矛盾，并发布给 Claude 的持久执行通知。

## 验收标准
- [ ] README 首页可直接看到可执行规则、验证分类与当前工具状态。
- [ ] D-0008 记录人类批准；AGENTS.md、CLAUDE.md、协作手册及被替代决策一致。
- [ ] Claude 收到可追踪的执行通知，入口要求读取；不把通知发布等同于已读确认。
- [ ] 文档链接、协作校验、差异检查与独立复核通过，按纯文档门禁发布。

## 背景 / 约束 / 参考
通知与人工队列：https://github.com/brianabc121/WorkspaceCore/issues/235 。本次只发布规则文档，不更改业务代码、workflow 或编号工具，不声称自动化已上线。短计划：docs/superpowers/plans/2026-11-16-stable-pr-rules.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 16:47 UTC · codex · 创建任务（owner: codex）
- 2026-11-16 16:48 UTC · codex · 开始任务
- 2026-11-16 16:48 UTC · codex · 人类已在当前对话批准稳定 PR、统一集成与分层 CI 方案，并明确要求写入首页 README、通知 Claude。本次修改协作协议和 Claude 入口均在授权范围内；先发布可人工执行的规则，工具自动化明确标为待实现，不更改业务代码或 workflow。复用现有隔离目录。
- 2026-11-16 17:00 UTC · codex · 规则与首页入口已写完；通知 #235 已发布且登记本次启动窗口，T-0268 已正式交接给 Claude（源提交待首次文档提交后固定）。相对链接、协作与差异检查已执行，产品代码、workflow、解析器无改动；接下来独立复核和轻量发布。
- 2026-11-16 17:04 UTC · codex · 独立复核指出手册后半段残留旧编号、作者同步main和全绿门禁说明。已逐项核对并统一命令示例、FAQ、分工参考和默认审查例外；没有更改旧决策正文或已提交日志。
