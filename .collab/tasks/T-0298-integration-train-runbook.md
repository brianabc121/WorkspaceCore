---
id: T-0298
title: 合并主干提速：业内做法调研、D-0009 批量车次集成提案与版本更新执行手册
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: .collab/decisions/D-0009-batch-integration-train.md,docs/guides/integration-runbook.md,docs/decisions/2026-11-18-merge-queue-research.md,.collab/tasks/T-0298-integration-train-runbook.md
depends_on:
created: 2026-11-18
updated: 2026-11-18
---

# T-0298 合并主干提速：业内做法调研、D-0009 批量车次集成提案与版本更新执行手册

## 目标
人类 2026-11-18 在对话中说：会有十几个开发好的需求等着合并，其中有些因为前面的 PR 跑 CI 很久，后面的都合不进去。
先要求评估现有合并方案是否合理，然后要求（原话）：“给我参考下业内专家他们是怎么做的。再写一份方案放到github上作为版本更新执行手册”。

按 #235 的记录，瓶颈是：集成窗口一次只合一个 PR，合并后要等 main 的全套推送检查（约 1 小时）才释放；
`ci-api / api` 约 56 分钟、`ci-infra / agents` 约 50 分钟；偶发失败每次再加约 1 小时。吞吐上限约每小时 1 个 PR。

本任务交付：
1. 业内做法调研记录（带来源和核对日期）；
2. 协作决策提案 D-0009（`proposed`，人类批准后生效）：批量“车次”集成、测过即发布、剔除 / 二分、停线；
3. 版本更新执行手册：集成者和作者每一步怎么做、命令、#235 留言模板、已知偶发清单；
4. 后续工程任务清单（只列出，不在本任务里改 CI 或代码）。

## 验收标准
- [x] `docs/decisions/2026-11-18-merge-queue-research.md`：本仓库现状数据；bors / Rust rollup、GitHub 合并队列、Zuul、Uber SubmitQueue、Google TAP、Chromium CQ、Shopify、商业合并队列、CI 常规做法；每节附来源链接，写明核对日期和核对方式的限制
- [x] `.collab/decisions/D-0009-batch-integration-train.md`：`status: proposed`；只补充 D-0008，写明不变的条款、生效条件和代价
- [x] `docs/guides/integration-runbook.md`：作者就绪清单、集成者组车 / 预检 / 发布 / 失败 / 停线步骤和命令、冲突细则、#235 留言模板、时间预算、已知偶发清单；D-0009 才有的做法逐节标明“D-0009 批准后适用”
- [x] 手册里的组车脚本和预检命令在本机实际跑过（用 #258 的固定 head 试组车，不推送）
- [x] 不改 `docs/AGENTS.md` 第 1 ~ 7 节、README 首页的协作规则和 D-0008 正文（人类批准 D-0009 后另开 PR 改）
- [x] `python3 scripts/collab.py check` 通过；新文件里的相对链接都能打开

## 背景 / 约束 / 参考
- 编号 T-0298、D-0009 由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留。
- 纯文档 + 协作规则提案，按 D-0008 走纯文档门禁（`check`、`secrets`、相对链接与格式）。
- 调研时本会话的网络规则不允许直接打开 docs.github.com、zuul-ci.org、graydon2.dreamwidth.org，结论来自搜索结果给出的原文摘录，调研记录第 5 节写明了这一限制。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 19:13 UTC · claude · 创建任务（owner: claude）
- 2026-11-18 19:15 UTC · claude · 开始任务
- 2026-11-18 19:15 UTC · claude · 调研记录、D-0009（proposed）、执行手册写完；组车脚本用 #258 的固定 head 在本机试过（不推送），alembic heads、collab check、相对链接检查通过。按集成会话预留时的要求：D-0009 才有的做法逐节标明“批准后适用”，“测过即释放”等人类确认；不改 AGENTS.md 第 1~7 节、README 协作规则和 D-0008。
- 2026-11-18 19:15 UTC · claude · status in_progress → done：交付调研记录、D-0009 提案与执行手册；D-0009 等人类批准后生效，README / AGENTS 的指引另开 PR
