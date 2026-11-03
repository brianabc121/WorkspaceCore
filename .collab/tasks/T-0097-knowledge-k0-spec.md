---
id: T-0097
title: 知识库 K-0：前端库与 OpenClaw 2026.9.36 核验，规范修订（KNOWLEDGE_PLAN 第 11 节）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/DEVELOPMENT_SPEC.md,docs/AGENTS.md,docs/v2
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0097 知识库 K-0：前端库与 OpenClaw 2026.9.36 核验，规范修订（KNOWLEDGE_PLAN 第 11 节）

## 目标
人类 2026-11-03 批准 `docs/v2/KNOWLEDGE_PLAN.md`（“按推荐的来”）。实施前先完成两件事：
- K-0 核验：前端库的版本与许可证、OpenClaw 2026.9.36 的相关行为；
- 按方案第 11 节修订规范条文：智能体改知识库不审核、自建 Markdown 笔记编辑器。

## 验收标准
- [x] 核验记录 `docs/decisions/2026-11-03-knowledge-k0-verification.md`：
  - 库版本：CodeMirror 6、markdown-it、DOMPurify、d3-force 3.0.0、node-diff3；
  - OpenClaw：MCP instructions 不交给模型；工具描述截到 1200 字符；定时任务由运维用 CLI 建；IM 只放行管理员。
- [x] 方案的 3.9、3.10、第 7、10、11、12 节按核验结果与批准更新。
- [x] `DEVELOPMENT_SPEC.md` 1.2 / 1.3 / 7.2、`docs/AGENTS.md` 第 8 节（第 9 条、Owns、V2 补充规则）、`TRANSFORMATION_PLAN.md` 4.6 按第 11 节修订。
- [x] `python3 scripts/collab.py check` 通过。

## 背景 / 约束 / 参考
- 方案：`docs/v2/KNOWLEDGE_PLAN.md`（已批准）；核验：`docs/decisions/2026-11-03-knowledge-k0-verification.md`
- 效果图：https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 20:45 UTC · claude · 创建任务（owner: claude）
- 2026-11-03 20:45 UTC · claude · 开始任务
- 2026-11-03 20:45 UTC · claude · 人类在对话中指派：按推荐的来，全面落实。完成 K-0 核验（npm 注册表查版本；OpenClaw v2026.9.36 源码 70c40e4）与规范修订。
- 2026-11-03 20:45 UTC · claude · status in_progress → done：核验记录与规范修订完成
