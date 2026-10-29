---
id: D-0003
title: 按模块分工：codex 负责前端，claude 负责后端、平台与 Sprint 验收
status: accepted
date: 2026-10-29
author: claude
supersedes:
---

# D-0003 按模块分工：codex 负责前端，claude 负责后端、平台与 Sprint 验收

## 背景
`docs/DEVELOPMENT_SPEC.md` 原本按“Codex 一人严格按顺序实施”写成。现在由 Claude 和 Codex 两个代理共同开发，
需要决定怎么分工，才能并行推进、减少互相改同一批文件，并让每个 Sprint 的完成情况有人实际验证。

两边的运行环境差别很大，这是分工的主要依据：

| 能力 | Claude（云端会话） | Codex（云端任务） |
|---|---|---|
| 联网（npm / PyPI / 官方文档） | 可以 | 通常只在环境初始化阶段可以，代理运行时默认不联网 |
| Docker / Compose | 可以（能启动 dockerd、拉镜像） | 通常不可以 |
| 本地 PostgreSQL 16 / Redis 7 | 有 | 视环境而定 |

Codex 的实际环境以它在 AGENTS.md 第 9 节写下的备注为准。

## 决策
1. **按模块长期分 owner，而不是按 Sprint 分**：

   | 模块 | owner |
   |---|---|
   | 前端 `apps/web/**`（打包用的 `Dockerfile`、`.dockerignore` 除外）、`.github/workflows/ci-web.yml` | codex |
   | 后端 `services/api/**`、数据库迁移、Worker、Connector | claude |
   | 平台：`compose*.yaml`、`infra/**`、根目录 `Makefile` / `README.md` / `.env.example` / `.gitignore`、`apps/web/Dockerfile`、`ci-api.yml` / `ci-infra.yml` | claude |
   | 第三方 API / 版本核验 ADR（需要联网读官方文档） | claude |
   | 每个 Sprint 的联调与 DoD 验收，验收通过后勾选 `docs/TASKS.md` | claude |
   | 需求、路线图、开工指令（`docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md`、`docs/CODEX_START.md`）、是否进入下一个 Sprint | human |

   具体路径和接口约定见 `docs/decisions/2026-10-29-sprint-0-contracts.md`。

2. **每个 Sprint 的节奏**（从 Sprint 1 开始）：
   1. claude 先提交该 Sprint 的接口契约：Pydantic schema + 导出的 `services/api/openapi.json`；
   2. codex 用 `openapi.json` 生成 TypeScript 类型开发前端，claude 同时实现后端逻辑；
   3. claude 联调并按 DoD 实测验收，验收通过后勾选 `docs/TASKS.md`，交给人类决定是否进入下一个 Sprint。

3. **交叉审查**：claude 的 PR 请 codex 审，codex 的 PR 请 claude 审；审查意见写在 PR 上，人类决定合并。

4. 需要越过模块边界时（例如前端需要后端新增字段），在对方任务的日志里 `log` 提出，或建一个任务交给对方，
   不直接改对方的目录。

## 理由
- 后端、数据库、Compose 和第三方 API 核验都需要联网、Docker 或数据库才能真正验证；项目规则要求“不得虚构通过结果”，
  交给能实际运行的一方更可靠。
- 前端可以只靠 Node 工具链开发和测试，在不联网的沙箱里也能推进（依赖装好之后）。
- 按模块分 owner，两边的 PR 基本不会改同一个文件；契约先行，前端不用等后端写完。

## 代价 / 后果
- Sprint 0 的工作量偏向 claude（骨架和平台都在这一侧）；从 Sprint 2 起前端工作量明显增加，整体会更均衡。
- 每个 Sprint 都要先等契约 PR 合并，前端才能开始对接真实接口；在此之前前端可以先用 mock。
- 如果 Codex 的环境开通了联网或 Docker，可以重新评估分工，写新的决策取代本条。

## 考虑过的其他方案
- **Codex 实施全部、Claude 只做审查**（原 CODEX_START.md 的设想）：分工简单，但只有一个代理在写代码，
  且 Codex 环境里跑不了 `make up`，Sprint DoD 无法实测。
- **按 Sprint 轮流**：每次只有一个代理在干活，没有利用并行；而且上下文要在两边来回交接。
- **按功能纵向切（一人做完一个功能的前后端）**：交接最少，但两边会同时改 `services/api` 和 `apps/web`，冲突多。
