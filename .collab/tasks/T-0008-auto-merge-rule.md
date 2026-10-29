---
id: T-0008
title: 合并规则改为 claude 在条件满足时自动合并（D-0004）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/AGENTS.md, CLAUDE.md, .collab/README.md
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0008 合并规则改为 claude 在条件满足时自动合并（D-0004）

## 目标
人类要求以后做好后自动合并到 main，并选择让 claude 审查通过后也合并 codex 的 PR。
原规则“只有人类能合并”写在 AGENTS.md 红线里，必须同步修改，否则以后的会话仍会按旧规则停下来等人类。

## 验收标准
- [x] D-0004（人类已在对话中批准）：合并条件、codex PR 的审查流程、仍需人类决定的例外
- [x] docs/AGENTS.md：角色表、第 5 节收工第 4 步、第 6 节红线、第 7 节审查
- [x] CLAUDE.md：claude 的合并与审查做法；人类要求用中文回复
- [x] .collab/README.md：流程图、合并说明、分工表

## 背景 / 约束 / 参考
- 先合并了已全绿的 PR #5（T-0005），再提交本规则修改

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:12 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 22:12 UTC · claude · 开始任务：人类在对话中要求：以后做好后自动合并到 main；并选择 codex 的 PR 由 claude 审查后合并
- 2026-10-29 22:12 UTC · claude · status in_progress → done：规则已更新；本 PR 属于人类在对话中要求的协作规则修改，按 D-0004 第 3 节可以合并
