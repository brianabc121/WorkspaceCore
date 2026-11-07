---
id: T-0143
title: 多实例 M0 核验：OpenClaw 2026.9.36 在每个实例自己的网络里运行、两个实例共存、cgroup 指标、boot_id
owner: claude
status: done
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
- [x] OpenClaw 2026.9.36 在项目网络（bridge）里运行；隧道、取图代理用 service:openclaw 共用网络命名空间、各挂自己的 hosts 文件
- [x] 两个实例在同一台机器上共存、互不影响；一个实例连不到另一个的回环端口
- [x] cgroup v2 / v1 的 CPU、内存、进程数在锁定镜像（node 用户、去掉全部 capability）里可读；boot_id 与宿主机一致
- [x] 一个空闲 OpenClaw 的内存与磁盘占用（容量预检的建议值）
- [x] 核验记录 docs/decisions/2026-11-07-multi-openclaw-verification.md

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
- 2026-11-07 03:25 UTC · claude · 开始任务
- 2026-11-07 03:25 UTC · claude · 本地 Docker（cgroup v1）实测：boot_id、cgroup v1 的 CPU / 内存 / 进程数、项目网络里只看到自己的网卡、回环隔离、共用网络命名空间与各挂各的 hosts 文件都成立；写进核验记录第 2 节。cgroup v2、真实 OpenClaw 在项目网络里的行为、隧道、两个实例共存、空闲占用留给 CI（T-0148），见第 3 节。
- 2026-11-07 04:08 UTC · claude · 实施 T-0145 时补测（核验记录 2.1、2.2 节）：主容器崩溃后加入它网络命名空间的容器会断网（docker compose restart 会一起重启，崩溃不会），所以改为每个实例一个只运行 sleep 的 net 服务持有命名空间，OpenClaw、隧道、取图代理都加入它（验收标准第一条的 service:openclaw 改为 service:net）；compose run --no-deps 要先 up -d net；host-report.mjs 在锁定镜像里读 cgroup v1 的值与宿主机 HMAC 一致。还差：cgroup v2、真实 OpenClaw 在 net 命名空间里的 IM / MCP / 隧道、两个实例共存与空闲占用——本 PR 的 CI（agents 场景 19、SSH 部署验收）先测一部分，其余在 T-0148。
- 2026-11-07 07:31 UTC · claude · status in_progress → done：完成：核验记录 docs/decisions/2026-11-07-multi-openclaw-verification.md（本会话 cgroup v1 + CI cgroup v2 实测、两个实例共存与回环隔离、boot_id、空闲占用）。偏差：隧道与取图代理不用 service:openclaw，而是共用占位的 net 服务的命名空间（第 2.1 节：OpenClaw 重启时其他容器不断网），各挂自己的 hosts 文件。PR brianabc121/WorkspaceCore#169
