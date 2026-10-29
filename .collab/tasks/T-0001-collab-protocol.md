---
id: T-0001
title: 建立 Claude ↔ Codex 协作交接协议
owner: codex
status: handoff
priority: P1
branch: claude/cool-wright-99qbbg
paths: AGENTS.md, CLAUDE.md, README.md, scripts/collab.py, .collab/README.md, .collab/templates, .github
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0001 建立 Claude ↔ Codex 协作交接协议

## 目标
Claude 与 Codex 将在本仓库共同开发新项目。两边都没有跨会话记忆，也不能直接对话，
需要一套落在仓库里的流程，让人类和两个代理随时都能看清：每个任务在谁手上、做到哪了、
交接了什么，并且避免两边同时改同一块代码。

## 验收标准
- [x] AGENTS.md 写明协作规则，Codex 自动读取；CLAUDE.md 通过 `@AGENTS.md` 引用，Claude 自动读取
- [x] `.collab/` 目录：任务、交接单、决策三类文件及模板
- [x] `scripts/collab.py`：status / inbox / new / start / handoff / set / log / decision / check
- [x] CI（`.github/workflows/collab.yml`）运行 check 并输出看板；PR 模板
- [x] `.collab/README.md`：给人类的方案说明与操作手册
- [ ] Codex 在自己的环境里读到规则，跑通 inbox → start → set done
- [ ] Codex 对协议的意见已记录（没有意见也写明）
- [ ] 人类审阅后把 D-0001 改为 accepted

## 背景 / 约束 / 参考
- 决策：`.collab/decisions/D-0001-file-based-collab.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:05 UTC · claude · 应人类要求创建任务（owner: claude）
- 2026-10-29 21:05 UTC · claude · 开始任务：按人类要求设计并实现协作交接方案
- 2026-10-29 21:05 UTC · claude · 交接给 codex（continue）→ .collab/handoffs/2026-10-29-2105_T-0001_claude-to-codex.md：协作协议和工具已经搭好，请你在自己的环境里验收并试跑一遍完整的接手→完成流程，提出你这边的修改意见。
- 2026-10-29 21:30 UTC · claude · 备注给验收方：同一个 PR 里还加入了 T-0002（GitHub 交接提醒 / 看板 issue / 停滞提醒，以及 AGENTS.md 第 1、3 节的两处补充，见 D-0002），验收时请一并看看；D-0002 同样等人类批准。
- 2026-10-29 22:06 UTC · claude · 提示：T-0005（claude）正在修改 README.md 和 .github/workflows/ci-api.yml，与本任务声明的 paths 重叠；T-0001 的验收只需改 AGENTS.md 第 9 节和本任务文件，请不要改这两处
