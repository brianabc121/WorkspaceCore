---
id: T-0065
title: 完善 README 与新增部署教程（人类要求）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: README.md,docs/deploy-guide.md,docs/deployment.md,docs/architecture.md,infra/caddy,compose.yaml,compose.https.yaml,.env.example,scripts/restore-verify.sh,services/api/tests/test_https_config.py
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0065 完善 README 与新增部署教程（人类要求）

## 目标
README 还停留在早期（“this is a personal workspace”、进度描述零散），新读者看不出项目能做什么、怎么部署。
人类要求完善 README，并提供一份从零部署的教程。`docs/deployment.md` 是完整的运行手册（800+ 行），
教程按“一台新服务器 → 能用 → 可选组件 → 日常维护”的顺序串起来，细节链接回运行手册。

## 验收标准
- [x] README：项目简介、功能概览（V1 + V2）、架构图、技术栈、快速开始、文档索引、本机开发、目录结构、协作方式；去掉过时内容
- [x] 新增 `docs/deploy-guide.md` 部署教程：服务器要求与准备、安装 Docker、获取代码、生成并修改 `.env`、域名与 HTTPS、启动与迁移、首次安装、
  可选组件（Vikunja、GitHub、AFFiNE、监控、n8n、OpenIM + OpenClaw）、备份与恢复演练、升级与回滚、常见问题
- [x] 教程里的命令、变量名、端口都与仓库实际一致（逐条对照 Makefile、compose、`.env.example`、`docs/deployment.md`），按教程在本机走一遍关键步骤

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 19:12 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-01 19:12 UTC · human · owner unassigned → claude：人类在对话中指派：完善项目 README，并提供部署教程
- 2026-11-01 19:12 UTC · claude · 开始任务
- 2026-11-01 19:22 UTC · claude · README 重写（功能、架构图、快速开始、部署要点、文档索引、开发、目录、协作、许可证）；新增 docs/deploy-guide.md（12 步部署教程）。核对时发现运行手册的 HTTPS 做法不可用（只发布了 8080，且会断掉 Vikunja 的 http://caddy:8080 回调），补了正式的 HTTPS 模式：路由抽到 infra/caddy/routes.caddy，新增 https.Caddyfile 与 compose.https.yaml（.env 里 COMPOSE_FILE + SITE_DOMAIN + ACME_EMAIL），恢复演练固定只用 compose.yaml；本机用 SITE_DOMAIN=localhost 实测 HTTPS 200 + HSTS、HTTP 308 跳转、内部 :8080 正常；默认模式 make smoke 通过；make env 在全新目录生成新变量、重跑不改已有值。顺带更新 deployment.md（HTTPS、caddy 健康检查、迁移到 0018）与 architecture.md
- 2026-11-01 19:22 UTC · claude · status in_progress → done：README 与部署教程完成，HTTPS 模式实测可用
