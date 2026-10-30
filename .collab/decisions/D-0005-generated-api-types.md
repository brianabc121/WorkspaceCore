---
id: D-0005
title: 前端生成的接口类型 schema.d.ts 由 claude 随契约一起提交
status: accepted
date: 2026-10-30
author: claude
supersedes:
---

# D-0005 前端生成的接口类型 schema.d.ts 由 claude 随契约一起提交

## 背景
T-0012（codex，PR #16）让前端从后端契约生成类型：`apps/web/src/services/schema.d.ts` 由 `services/api/openapi.json`
经 `npm run generate:api` 生成，Web CI 用 `npm run check:api` 检查二者一致。于是后端修改契约的 PR 如果不同时提交
重新生成的 `schema.d.ts`，Web CI 就会失败；而这个文件位于 D-0003 划给 codex 的 `apps/web` 目录。
Sprint 1 验收（T-0013）把这个问题交给人类决定。2026-10-30 人类在对话中决定：**schema.d.ts 由 claude 提交**。
本决策由人类在对话中作出，取代 D-0003 中 `apps/web` 目录归属对这一个生成文件的适用。

## 决策
1. 契约（`services/api/openapi.json`）只由 claude 修改（D-0003 第 2 条：契约先行）。claude 每次修改契约，
   都在**同一个 PR** 里运行 `cd apps/web && npm run generate:api`，并提交重新生成的 `apps/web/src/services/schema.d.ts`。
2. `schema.d.ts` 只能由上述命令生成，任何一方都不手工编辑；codex 的 PR 不修改它。
3. codex 需要改契约时，在任务日志里写明需求或交给 claude（`handoff --type continue`），由 claude 改契约并重新生成。
4. 除 `schema.d.ts` 外，`apps/web` 的其余文件仍归 codex（D-0003 不变）；claude 只运行生成命令，不改前端代码。
5. 如果 `openapi-typescript` 的版本或生成参数（`apps/web/package.json` 的 `generate:api`）需要调整，仍由 codex 负责。

## 理由
- 契约和生成类型必须原子地一起变化，否则 main 上的 Web CI 会失败；让修改契约的一方顺手重新生成最简单，也不需要两个 PR 串行。
- 生成文件是机械产物，内容完全由 `openapi.json` 和固定版本的生成器决定，提交方不需要理解前端实现。
- 人类明确选择了这一方案。

## 代价 / 后果
- claude 的契约 PR 会出现 `apps/web` 下的一个文件改动；审查时只需确认 `npm run check:api` 通过（即内容确由命令生成）。
- 前端可能因为类型变化而类型检查失败：claude 在契约 PR 里应同时运行 `npm run typecheck`；如果前端代码需要跟着改，
  由 codex 在对应的前端任务里处理，契约 PR 先以不破坏 main 为前提（例如只新增端点、不删除前端正在使用的字段）。

## 考虑过的其他方案
- **由修改契约的一方提交（不限定是 claude）**：实际上只有 claude 改契约，等价但不如直接写明清楚。
- **codex 在前端任务里跟进重新生成**：契约 PR 合并后到 codex 跟进前，main 上 Web CI 会是红的，不采用。
- **去掉 `check:api`**：失去契约与前端类型一致性的保证，不采用。
