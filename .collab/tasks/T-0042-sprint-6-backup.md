---
id: T-0042
title: Sprint 6 备份、隔离恢复与演练脚本：make backup / make restore-verify、加密、保留 7/30/90 日、A15 自动化（S6-04）
owner: claude
status: todo
priority: P1
branch:
paths: scripts,Makefile,infra/backup,compose.yaml,.env.example,docs/deployment.md,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0041
created: 2026-10-31
updated: 2026-10-31
---

# T-0042 Sprint 6 备份、隔离恢复与演练脚本：make backup / make restore-verify、加密、保留 7/30/90 日、A15 自动化（S6-04）

## 目标
实现规范 11.3 的 `make backup` 与 `make restore-verify`，以及 11.4 的备份策略：工作台数据可以定期、加密地备份到异地，
并且能在隔离的临时环境里恢复、通过 Smoke Test（A15、Sprint 6 DoD“能从备份恢复并通过 Smoke Tests”）。

## 验收标准
- [ ] `make backup`：按 T-0041 选定的方案生成加密备份（PostgreSQL 逻辑备份 + 必要的配置），写清楚备份内容与不备份的内容；不中断服务；失败时非零退出并给出原因
- [ ] 保留策略可配置（默认 7 / 30 / 90 日），清理过期备份；异地目标可配置（本地目录可用于测试），凭据只从环境变量或 Secret 读取
- [ ] `make restore-verify`：在隔离的临时 Compose 项目（不同的项目名、网络与卷，不影响正在运行的栈）里恢复最新备份，运行迁移检查与 Smoke Test，结束后清理临时环境
- [ ] A15 自动化并纳入 CI：写入用户、工作空间、首页布局与几类业务数据 → 备份 → 在空环境恢复 → 登录成功、首页布局与数据一致、Smoke Test 通过
- [ ] 恢复演练脚本与文档：每月演练的步骤、预期时间、出错时的处理；加密密钥与备份分开保存的说明
- [ ] 日志与备份里没有明文密钥；`.env.example` 只有占位符

## 背景 / 约束 / 参考
- 依赖：T-0041（工具与格式的核验）
- 规范：第 11.3、11.4 节，第 15 节 A15，第 18 节
- **禁止把删除 volumes 作为常规停机命令**（规范 11.3）：恢复演练只删除自己创建的临时项目

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
