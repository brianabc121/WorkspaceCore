---
id: D-0004
title: PR 合并改由 claude 在条件满足时执行；codex 的 PR 经 claude 审查后合并
status: accepted
date: 2026-10-29
author: claude
supersedes:
---

# D-0004 PR 合并改由 claude 在条件满足时执行；codex 的 PR 经 claude 审查后合并

## 背景
原规则（AGENTS.md 红线、D-0003 第 3 条）规定只有人类能合并 PR。2026-10-29 人类在对话中要求：
“以后做好后自动合并到 main”；并选择让 claude 审查通过后也合并 codex 的 PR（Codex 的沙箱通常无法自己合并）。
本决策由人类在对话中批准，取代 D-0003 第 3 条里“人类决定合并”的部分。

## 决策

### 1. claude 自己的 PR
同时满足以下条件时，claude 直接合并到 main，不再等待人类：
- 最新提交上的**所有** CI 检查都成功（不是“进行中”，也不是“跳过”）；
- 与 main 没有冲突；
- PR 上没有未处理的阻塞审查意见。

### 2. codex 的 PR
1. claude 按 D-0003 做交叉审查，意见写在 PR 上；
2. 有阻塞问题：在 PR 上逐条写明，并在任务日志里 `log`，或用 `rework` 交还 codex，**不合并**；
3. 没有阻塞问题，且满足第 1 节的全部条件：claude 合并。

### 3. 仍然要人类决定、不自动合并的情况
- PR 修改了人类负责的文件（`docs/DEVELOPMENT_SPEC.md`、`docs/CODEX_START.md`），而人类没有在对话中要求这次修改；
  Sprint 验收任务勾选 `docs/TASKS.md` 不算在内。
- PR 修改了协作协议本身（AGENTS.md 1–7 节、`.collab/templates/`、`scripts/collab*.py` 的字段与状态集合），
  而人类没有在对话中要求或批准。
- PR 包含删除或不可逆改写已有数据的迁移。
- 进入下一个 Sprint：Sprint 验收任务合并后，仍 escalate 给人类，人类批准后才建下一个 Sprint 的任务。

遇到这些情况：PR 保持打开，用 `handoff --to human --type escalate` 或在对话里说明需要人类决定什么。

### 4. 合并方式
- 使用 merge commit（保留提交号，交接单里引用的提交才有效）；不 squash、不 rebase、不 force-push。
- 绝不绕过失败的检查，不为了合并去跳过或禁用测试。
- 合并后确认 main 上的 `collab`、`collab-sync` 等工作流运行成功；失败按 CI 失败处理。

## 理由
- 人类明确要求减少等待；PR 的质量把关落在 CI 与交叉审查上，而这两项都能自动完成。
- Codex 无法自己合并；由 claude 审查后合并，可以保证每个 PR 至少被另一方看过一次。
- 第 3 节保留了真正需要人类判断的情形：需求范围、协作规则、数据安全、Sprint 推进。

## 代价 / 后果
- claude 自己的 PR 不再等待 codex 的审查（codex 的审查只能由人类触发），合并前的独立复核变少。
  人类仍可在 PR 下评论 `@codex review`，审查发现的问题按新任务或 `rework` 处理。
- 合并错误的 PR 只能事后 revert；因此合并条件必须严格按第 1 节执行。

## 考虑过的其他方案
- **GitHub 的 auto-merge 功能**：需要先配置分支保护，且不能表达第 3 节的例外，暂不采用。
- **只合并 claude 自己的 PR**：codex 的 PR 仍需人类点合并，人类选择了不采用。
