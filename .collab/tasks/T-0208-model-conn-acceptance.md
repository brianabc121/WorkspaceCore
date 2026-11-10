---
id: T-0208
title: 中转站管理（MC-8）：端到端验收、迁移演练与文档
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,docs,.github/workflows/ci-infra.yml
depends_on: T-0200, T-0201, T-0202, T-0203, T-0204, T-0205, T-0206, T-0207
created: 2026-11-09
updated: 2026-11-10
---

# T-0208 中转站管理（MC-8）：端到端验收、迁移演练与文档

## 目标
端到端验收、迁移演练与文档：验收矩阵逐项附证据，api-contract、部署与迁移文档更新。（MC-8）方案见 `docs/v2/model-connections/`（设计、接口约定、三个实施计划、迁移手册、验收矩阵、效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。

## 验收标准
- [ ] 验收矩阵每项写明测试名 / 命令 / 结果，单元、真实 OpenClaw + 假上游、真实服务商冒烟分开
- [ ] 迁移 dry-run → apply → report 演练记录（脱敏）
- [ ] 后端、前端、collab check 与 CI 真实输出写进 PR
- [ ] api-contract、部署指南与核验记录更新

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 23:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 01:37 UTC · claude · 开始任务
- 2026-11-10 01:38 UTC · claude · 核验记录第 3、5 节已写（本地结果、迁移演练、验收矩阵）；等后端全量与 CI 结果写入第 3、4 节后标 done
