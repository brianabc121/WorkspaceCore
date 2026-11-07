---
id: T-0152
title: 新闻早报 N1 部署：TrendRadar compose、news 内部网络与配置卷、提示词下载、Make、deploy.sh
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: infra/trendradar,compose.yaml,Makefile,scripts,.env.example,docs
depends_on: T-0151
created: 2026-11-07
updated: 2026-11-07
---

# T-0152 新闻早报 N1 部署：TrendRadar compose、news 内部网络与配置卷、提示词下载、Make、deploy.sh

## 目标
把 TrendRadar 作为可选组件部署：锁定 digest 的两个容器只在工作台内部网络 news 上，配置卷由工作台写、容器只读；上游提示词部署时下载并校验；Make 与 deploy.sh 一键安装 / 升级。

## 验收标准
- [ ] infra/trendradar/compose.yaml：两个容器锁定 digest、不发布端口、配置只读挂载、抓取容器能出网
- [ ] core 的 news 内部网络与 trendradar-config 卷，api / worker 读写挂载
- [ ] make trendradar-pull / trendradar-prompts / trendradar-up / trendradar-down / trendradar-logs
- [ ] deploy.sh 可选组件“新闻早报”，安装与升级时启动
- [ ] 部署指南与部署说明

## 背景 / 约束 / 参考
人类 2026-11-07 在对话中给出设计文档《Workspace 接入 TrendRadar 升级方案》（https://claude.ai/artifact/KYLXzXTfMXh1QLJob9Teim），要求「按照这份设计方案进行全面落实」。落实方案见 docs/v2/NEWS_PLAN.md，核验见 docs/decisions/2026-11-07-trendradar-verification.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 16:07 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 16:55 UTC · claude · 开始任务
- 2026-11-07 16:55 UTC · claude · infra/trendradar/compose.yaml（锁定 digest、news 内部网络、cap_drop ALL + group_add 10001）、scripts/trendradar-prompts.sh（锁定提交 + sha256）、Make 目标、core 网络与卷、api 镜像目录、deploy.sh 可选组件“新闻早报”、部署手册第 23 节。本地实测整条链路通过（见核验记录第 7 节）
