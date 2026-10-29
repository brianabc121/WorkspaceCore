---
id: T-0003
title: 整合人类提交的项目规范（caf32fc）与协作协议
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: AGENTS.md, CODEX_START.md, .github/pull_request_template.md, .collab/README.md
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0003 整合人类提交的项目规范（caf32fc）与协作协议

## 目标
人类在 PR #1 开着时直接向 main 推送了项目规范（caf32fc：AGENTS.md、CODEX_START.md、docs/DEVELOPMENT_SPEC.md、
docs/TASKS.md、DEVELOPMENT_SPEC.docx），与 PR #1 的 AGENTS.md 冲突。需要在不丢失任何一方内容的前提下合并，
并让项目规范与协作协议互相衔接。

## 验收标准
- [x] AGENTS.md：人类的项目规则原文完整放入第 8 节（只调整了标题层级），声明第 8 节优先于 1–7 节
- [x] 第 1 节说明 docs/TASKS.md（路线图）与 .collab/tasks（谁在做）的分工；技术 ADR 进 docs/decisions/
- [x] 第 4 节提交信息改用项目规范第 14 节的格式并带任务号
- [x] PR 模板改为第 8 节“提交格式”的结构
- [x] 按 CODEX_START.md 建立 Sprint 0 协作任务 T-0004（owner: codex），并在 CODEX_START.md 末尾追加协作流程说明（原文不动）
- [x] 用 merge 而不是 rebase 合入 main，交接单里引用的提交号保持有效

## 背景 / 约束 / 参考
- 人类提交：caf32fc（docs: add development specification and Codex task documents）
- 未修改人类的 docs/DEVELOPMENT_SPEC.md、docs/TASKS.md、DEVELOPMENT_SPEC.docx

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:37 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 21:37 UTC · claude · 开始任务：PR #1 与 main 上人类新推送的 caf32fc 在 AGENTS.md 冲突，合并时整合两份内容
- 2026-10-29 21:37 UTC · claude · status in_progress → done：冲突已按验收标准整合；未改动人类的规范文档
