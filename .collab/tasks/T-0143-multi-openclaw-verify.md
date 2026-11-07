---
id: T-0143
title: 多实例 M0 核验：OpenClaw 2026.9.36 在每个实例自己的网络里运行、两个实例共存、cgroup 指标、boot_id
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs,services/api/app/agent_bundle,services/api/tests,tests/e2e
depends_on: T-0142
created: 2026-11-07
updated: 2026-11-07
---

# T-0143 多实例 M0 核验：OpenClaw 2026.9.36 在每个实例自己的网络里运行、两个实例共存、cgroup 指标、boot_id

## 目标
在锁定版本上实测多实例方案依赖的行为（方案第 13 节），写核验记录。

## 验收标准
- [ ] OpenClaw 2026.9.36 在项目网络（bridge）里运行；隧道、取图代理用 service:openclaw 共用网络命名空间、各挂自己的 hosts 文件
- [ ] 两个实例在同一台机器上共存、互不影响；一个实例连不到另一个的回环端口
- [ ] cgroup v2 / v1 的 CPU、内存、进程数在锁定镜像（node 用户、去掉全部 capability）里可读；boot_id 与宿主机一致
- [ ] 一个空闲 OpenClaw 的内存与磁盘占用（容量预检的建议值）
- [ ] 核验记录 docs/decisions/2026-11-07-multi-openclaw-verification.md

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
- 2026-11-07 03:25 UTC · claude · 开始任务
- 2026-11-07 03:25 UTC · claude · 本地 Docker（cgroup v1）实测：boot_id、cgroup v1 的 CPU / 内存 / 进程数、项目网络里只看到自己的网卡、回环隔离、共用网络命名空间与各挂各的 hosts 文件都成立；写进核验记录第 2 节。cgroup v2、真实 OpenClaw 在项目网络里的行为、隧道、两个实例共存、空闲占用留给 CI（T-0148），见第 3 节。
