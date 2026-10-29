---
id: T-0007
title: 规划两代理分工与 Sprint 0 任务拆分；修复 AGENTS.md 移入 docs/ 后的自动加载
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: AGENTS.md, CLAUDE.md, docs/AGENTS.md, docs/CODEX_START.md, docs/decisions/2026-10-29-sprint-0-contracts.md, .collab/README.md, scripts/collab.py
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0007 规划两代理分工与 Sprint 0 任务拆分；修复 AGENTS.md 移入 docs/ 后的自动加载

## 目标
人类要求规划并分配任务后开工。原规范按 Codex 一人实施写成，需要在两个代理之间分工，并把 Sprint 0 拆成可以并行的任务。
同时，人类在 8022546 把 AGENTS.md 移到了 docs/，导致 Codex 读不到根目录规则、CLAUDE.md 的 @AGENTS.md 失效，需要修复。

## 验收标准
- [x] D-0003：按模块分工（codex 前端；claude 后端、平台、第三方核验与 Sprint 验收）
- [x] 接口契约 ADR：`docs/decisions/2026-10-29-sprint-0-contracts.md`
- [x] Sprint 0 拆为 T-0004（codex 前端）、T-0005（claude 后端与骨架，可与 T-0004 并行）、T-0006（claude 集成与验收）
- [x] `docs/CODEX_START.md` 调整为前端范围，人类的限制条款原文保留
- [x] `docs/TASKS.md` 改为由 Sprint 验收任务统一勾选（docs/AGENTS.md 第 1 节）
- [x] 根目录 `AGENTS.md` 改为指向 `docs/AGENTS.md` 的链接；`CLAUDE.md` 改为 `@docs/AGENTS.md`
- [x] `collab.py check`：根目录缺少 AGENTS.md 或 CLAUDE.md 引用的文件不存在时报错

## 背景 / 约束 / 参考
- 人类提交：8022546（docs: organize platform design documents under docs）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:50 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 21:50 UTC · claude · 开始任务：人类要求：规划并分配任务后开工
- 2026-10-29 21:50 UTC · claude · status in_progress → done：规划完成；等人类确认分工后合并
- 2026-10-29 21:51 UTC · claude · 人类在对话中批准分工方案，D-0003 记为 accepted；同意合并后开工
