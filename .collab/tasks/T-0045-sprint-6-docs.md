---
id: T-0045
title: Sprint 6 文档：部署文档、版本表、升级 / 回滚说明、发布 checklist（S6-06）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs,README.md,compose.yaml,infra/vikunja/compose.yaml,infra/n8n/compose.yaml,services/api/Dockerfile,apps/web/Dockerfile,.github/workflows/ci-api.yml
depends_on: T-0042, T-0043, T-0044
created: 2026-10-31
updated: 2026-10-31
---

# T-0045 Sprint 6 文档：部署文档、版本表、升级 / 回滚说明、发布 checklist（S6-06）

## 目标
S6-06：发布前把文档补齐，让人类按文档就能在新机器上部署、升级、回滚和恢复，并有一份发布前逐项检查的清单。

## 验收标准
- [x] `docs/deployment.md` 更新到 Sprint 6 的状态：去掉“尚未实现”的旧说明，补上备份恢复、安全扫描、性能基准的用法
- [x] 版本表：所有镜像与关键依赖的锁定版本（tag + digest）、许可证与核验 ADR 的链接，集中在一处
- [x] 升级 / 回滚说明：数据库迁移的升级与回退策略、升级前备份、失败时如何回滚到上一版本
- [x] 发布 checklist：逐项可勾选（CI 全绿、扫描无高危、备份恢复演练通过、密钥轮换、`.env` 检查、版本锁定等）
- [x] README 的快速开始与文档链接保持正确

## 背景 / 约束 / 参考
- 依赖：T-0042、T-0043、T-0044（文档写的是它们的最终用法）
- 规范：第 11 节，第 13 节 Sprint 6，第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 08:08 UTC · claude · 开始任务
- 2026-10-31 08:21 UTC · claude · paths 加上 compose 文件、两个 Dockerfile 与 ci-api.yml：版本表要求所有镜像 tag + digest，原来有 5 个镜像只写了 tag，就在本任务锁定（ADR 2026-10-31-release-versions-and-licenses 第 1 节）；这些文件没有其他进行中的任务
- 2026-10-31 08:21 UTC · claude · status in_progress → done：五项验收标准完成：deployment.md 更新到 Sprint 6（第 5 节升级 / 回滚 / 迁移约定，命令实测过）；docs/versions.md 版本表（全部镜像 tag + digest、依赖、许可证与核验来源）；docs/release-checklist.md；README 进度、文档索引与目录；顺带锁定 5 个只写 tag 的镜像，更新 architecture / threat-model / data-dictionary 的过时状态；Redis 7.4 的 RSALv2 / SSPLv1 许可待人类决定（ADR 第 3 节）
