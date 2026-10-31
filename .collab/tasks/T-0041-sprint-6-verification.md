---
id: T-0041
title: Sprint 6 核验：备份与恢复工具、加密与异地存储、安全扫描与性能基准工具（锁定版本与 digest、许可证）（S6-04、S6-05）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/deployment.md
depends_on: T-0040
created: 2026-10-31
updated: 2026-10-31
---

# T-0041 Sprint 6 核验：备份与恢复工具、加密与异地存储、安全扫描与性能基准工具（锁定版本与 digest、许可证）（S6-04、S6-05）

## 目标
Sprint 6 的备份恢复（S6-04）与安全扫描、性能基准（S6-05）都要引入新的工具。项目规则禁止猜测第三方工具的版本与行为：
先锁定版本，核验要用到的命令、格式与安全边界，写成 ADR，作为 T-0042、T-0044 的依据（参照 T-0018、T-0025、T-0033 的做法）。

## 验收标准
- [x] PostgreSQL 逻辑备份：用与服务端同版本的 `pg_dump` / `pg_restore`（`postgres:16.15-alpine3.24` 镜像自带），确定格式（custom / directory）、一致性快照、角色与扩展（`pgcrypto`、`pg_trgm`）的恢复方式，实测一次备份 → 空库恢复
- [x] 加密、异地与保留（规范 11.4：加密、异地保存、保留 7/30/90 日可配置）：比较并选定方案（例如 restic 的仓库加密与 `forget` 保留策略，或 age 加密 + 自管保留），锁定 release（镜像 tag + digest 或二进制校验和）与许可证；密钥如何与备份分开保存
- [x] 备份范围：工作台数据库、`.env` 里与数据解密相关的密钥（`CREDENTIAL_ENCRYPTION_KEY` 等，单独加密保存）、Caddy 证书卷；Redis 只是缓存与队列、是否需要备份写明理由；Vikunja（`vikunja dump`）、n8n、AFFiNE 等独立部署的组件写明各自的备份方式与边界
- [x] 安全扫描：Python 依赖（例如 `pip-audit` 或 `uv` 自带的审计）、npm 依赖（`npm audit`）、密钥泄露扫描、容器镜像漏洞扫描，各自锁定版本，确定在 CI 里怎么运行、哪些级别算失败、误报怎么登记
- [x] 性能基准：选定工具（例如 k6，或 Python + httpx 自写脚本），确定规范第 12 节两个目标（基础 API P95 < 300 ms、缓存命中的首页 P95 < 500 ms）的测法：请求组合、并发、预热、统计口径
- [x] 所有结论附来源链接（官方文档、源码 tag、运行实例实测）；无法核验的项标为未验证

## 背景 / 约束 / 参考
- 规范：第 11.3、11.4 节，第 12 节，第 13 节 Sprint 6，第 15 节 A15，第 17 节（核验清单）
- 联网核验与 Docker 由 claude 负责（D-0003、D-0007）；不在对话里向人类索取凭据
- 参照 `docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 06:06 UTC · claude · 开始任务
- 2026-10-31 06:14 UTC · claude · 核验完成：docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md。PostgreSQL 16.15 自带 pg_dump 的 custom 格式备份 → 空库 pg_restore 实测一致（含扩展与 alembic_version）。备份选 restic 0.19.1（BSD-2，按 digest 锁定）：仓库加密、本地 / S3 / SFTP 等后端、forget --keep-within-daily 7d / weekly 30d / monthly 90d 用 27 个伪造日期的快照实测符合 7/30/90 日；.env 与 Redis 不进仓库（理由见 ADR 第 4 节）。扫描：pip-audit 2.10.1 无漏洞；npm audit 在 apps/web 报 zod <4.6.6 高危（@vee-validate/zod 只支持 zod 3，T-0044 处理）；gitleaks v8.30.1 只有 5 处测试假密钥与 OpenAPI 示例 token（T-0044 加允许列表）；trivy 0.76.1：api 镜像 0、web 镜像 3（Alpine 包，T-0044 修）、第三方锁定镜像只报告。性能基准用 Python 标准库脚本。未核验：restic 的异地后端（无存储与凭据）。
- 2026-10-31 06:14 UTC · claude · status in_progress → done：Sprint 6 核验 ADR 完成，T-0042 / T-0044 可以开始
