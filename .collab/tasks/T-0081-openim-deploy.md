---
id: T-0081
title: 一键部署脚本部署 OpenIM（deploy.sh connect 构建并启动 OpenIM，与 OpenClaw 接入入口解耦）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,Makefile,infra/openim,services/api/app/commands/connect.py,services/api/tests,docs
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0081 一键部署脚本部署 OpenIM（deploy.sh connect 构建并启动 OpenIM，与 OpenClaw 接入入口解耦）

## 目标
人类要求（2026-11-02）：一键部署脚本也要把 OpenIM 部署起来。之前 OpenIM 要按手册手动构建镜像、先部署 OpenClaw 接入入口
（准备证书）、手动改 `.env`，一键脚本不管。

## 验收标准
- [x] `deploy.sh connect`（安装结束时也会运行）询问并部署 OpenIM：构建补丁镜像（已有则跳过，Docker Hub 失败时改用 mirror.gcr.io 再试）、
      `make openim-up`、填 `.env` 的 `OPENIM_API_URL` / `OPENIM_OBJECT_URL` 并重启 api / worker、确认工作台连得上；内存不足 4 GB 默认不装
- [x] OpenIM 不再依赖 OpenClaw 接入入口：`agent-im` 网络挪到 `infra/openim/compose.agents.yaml`，网络存在时自动叠加；
      `make agents-up` 在 OpenIM 已运行时让 openim-server 加入
- [x] `connect status` 输出 `openim=none|ok|unavailable`；`deploy.sh status` / `upgrade` 处理 OpenIM
- [x] 测试（connect status、配置检查）、沙箱实测、文档（部署手册第 17 节、部署教程、ADR）

## 背景 / 约束 / 参考
- 调研笔记：OpenIM 的镜像只能在服务器上构建（官方镜像有越权问题，OC-0 ADR 第 4 节）；`agent-im` 由 agent-gateway 创建。
- `docs/decisions/2026-11-02-deploy-connect.md` 第 7 节。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 20:35 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 20:35 UTC · claude · 开始任务
- 2026-11-02 20:35 UTC · claude · 人类在对话中要求一键部署脚本部署 OpenIM。实现与沙箱验证完成（独立项目 t81、网段 172.31.244/245）：deploy.sh connect 部署 OpenIM 后 openim=ok、IM 状态 available、运维页 ok；重复运行跳过；之后 make agents-up 让 openim-server 加入 agent-im；upgrade_openim 正常。没验证：从零构建镜像（CI 覆盖）、低内存默认值、Docker Hub 限流重试
- 2026-11-02 20:35 UTC · claude · status in_progress → done：验收标准全部完成
