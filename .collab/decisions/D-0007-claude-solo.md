---
id: D-0007
title: codex 暂停期间由 claude 独立完成所有任务
status: accepted
date: 2026-10-31
author: claude
supersedes:
---

# D-0007 codex 暂停期间由 claude 独立完成所有任务

## 背景
2026-10-31 人类在对话中说：“codex没有额度了 你直接给我修吧。 从现在开始所有任务由你自己独立完成”。本决策由人类作出，claude 记录。

当时的状态：
- codex 的 PR #61（T-0039，通知中心 / SSE / 自动化页）有一个阻塞问题：api 重启后 SSE 不再重连。
- 这个问题已经交还给 codex（PR #61 的审查意见、issue #50 的 C-5），但 codex 没有额度继续做。

## 决策
1. 从 2026-10-31 起，直到人类另行通知，**codex 暂停**：
   - 不再给 codex 分配新任务；
   - issue #50（“给 Codex 的指令”）标为暂停。
2. **claude 负责所有模块**，包括 D-0003 里归 codex 的 `apps/web/**` 与 `.github/workflows/ci-web.yml`。
   - 前端改动沿用现有的技术栈、目录结构与代码风格，不顺手重写 codex 的代码；
   - 仍遵守 D-0005：不手改 `apps/web/src/services/schema.d.ts`，用 `npm run generate:api` 生成。
3. **codex 未完成的工作由 claude 接手**：
   - 先用 `collab.py set … --as human --owner claude` 记录人类的指派，再 `start`；
   - 不往 codex 的分支推送：把它的提交合进 claude 自己的分支，在上面修改，再开 PR。这样保留了 codex 的提交记录，合并后 codex 原来的 PR 也会显示为已合并。
4. **审查与合并**：
   - D-0004 里“codex 的 PR 由 claude 审查”暂不适用；claude 自己的 PR 照 D-0004 合并，D-0004 第 3 节的例外仍先问人类；
   - 前端改动合并前，要跑 `apps/web` 的 `npm run lint`、`typecheck`、`test`、`build`、`check:api`，以及相关的全栈浏览器验收。
5. **恢复**：人类决定让 codex 恢复时，另写一份决策取代本决策（或把本决策改为 superseded），并在 issue #50 写明 codex 从哪里接手。

## 理由
- 人类的决定：codex 没有额度，等待会让 Sprint 5 的验收和之后的任务都卡住。
- 不往 codex 的分支推送、保留它的提交，符合 AGENTS.md 第 6 节的红线，以后也能追溯谁写了什么。

## 代价 / 后果
- 没有了两个代理之间的交叉审查。用 CI 里的全栈验收与浏览器验收弥补，PR 里如实写明是自己审查。
- 前端与后端都由同一个会话完成，每个任务的工作量更大，PR 也会相应拆小。

## 考虑过的其他方案
- 等 codex 的额度恢复后由它修复：人类否决。
