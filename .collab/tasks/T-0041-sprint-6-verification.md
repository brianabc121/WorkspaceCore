---
id: T-0041
title: Sprint 6 核验：备份与恢复工具、加密与异地存储、安全扫描与性能基准工具（锁定版本与 digest、许可证）（S6-04、S6-05）
owner: claude
status: in_progress
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
- [ ] PostgreSQL 逻辑备份：用与服务端同版本的 `pg_dump` / `pg_restore`（`postgres:16.15-alpine3.24` 镜像自带），确定格式（custom / directory）、一致性快照、角色与扩展（`pgcrypto`、`pg_trgm`）的恢复方式，实测一次备份 → 空库恢复
- [ ] 加密、异地与保留（规范 11.4：加密、异地保存、保留 7/30/90 日可配置）：比较并选定方案（例如 restic 的仓库加密与 `forget` 保留策略，或 age 加密 + 自管保留），锁定 release（镜像 tag + digest 或二进制校验和）与许可证；密钥如何与备份分开保存
- [ ] 备份范围：工作台数据库、`.env` 里与数据解密相关的密钥（`CREDENTIAL_ENCRYPTION_KEY` 等，单独加密保存）、Caddy 证书卷；Redis 只是缓存与队列、是否需要备份写明理由；Vikunja（`vikunja dump`）、n8n、AFFiNE 等独立部署的组件写明各自的备份方式与边界
- [ ] 安全扫描：Python 依赖（例如 `pip-audit` 或 `uv` 自带的审计）、npm 依赖（`npm audit`）、密钥泄露扫描、容器镜像漏洞扫描，各自锁定版本，确定在 CI 里怎么运行、哪些级别算失败、误报怎么登记
- [ ] 性能基准：选定工具（例如 k6，或 Python + httpx 自写脚本），确定规范第 12 节两个目标（基础 API P95 < 300 ms、缓存命中的首页 P95 < 500 ms）的测法：请求组合、并发、预热、统计口径
- [ ] 所有结论附来源链接（官方文档、源码 tag、运行实例实测）；无法核验的项标为未验证

## 背景 / 约束 / 参考
- 规范：第 11.3、11.4 节，第 12 节，第 13 节 Sprint 6，第 15 节 A15，第 17 节（核验清单）
- 联网核验与 Docker 由 claude 负责（D-0003、D-0007）；不在对话里向人类索取凭据
- 参照 `docs/decisions/2026-10-31-prometheus-grafana-n8n-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 06:06 UTC · claude · 开始任务
