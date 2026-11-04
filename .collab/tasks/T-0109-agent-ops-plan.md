---
id: T-0109
title: 智能体能力补齐与 OpenClaw 服务器运维升级方案（任务与项目工具、项目群、日历待办、服务器状态、远程运维）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs
depends_on:
created: 2026-11-04
updated: 2026-11-04
---

# T-0109 智能体能力补齐与 OpenClaw 服务器运维升级方案（任务与项目工具、项目群、日历待办、服务器状态、远程运维）

## 目标
人类 2026-11-04 列出九项能力，要求核对部署好 OpenClaw 后是否都具备，核对后要求“先给我一份升级方案”。
本任务只写方案和核验记录，不改代码。

## 验收标准
- [x] `docs/v2/AGENT_OPS_PLAN.md`：九项的现状与缺口、每项的做法、安全与权限、要人类决定的事项（带推荐）、实施任务拆分、风险
- [x] `docs/decisions/2026-11-04-agent-ops-verification.md`：在锁定 OpenClaw 镜像里实测读宿主机指标；MCP 错误格式；Compose 子命令的核验计划；可复用的现有代码

## 背景 / 约束 / 参考
- 方案状态为“待批准”；人类批准（含 D1 ~ D5 的选择）后再按第 10 节建实施任务
- 第 9 项的网页终端不推荐（部署账号在 docker 组，等同于 root），需要人类单独决定

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 08:08 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 08:08 UTC · claude · 开始任务
- 2026-11-04 08:10 UTC · claude · 方案与核验 ADR 写好：九项现状（1、2、5 已具备；3、4、7 部分具备；6、8 没有；9 只有部署类动作），每项做法、D1 ~ D5 待决定、T-0110 ~ T-0114 实施拆分。在锁定 OpenClaw 镜像里实测（node 用户、host 网络、去掉全部 capability）：CPU 核心与负载、内存、两个分区、默认路由网卡流量、开机时长都与宿主机一致。
- 2026-11-04 08:10 UTC · claude · status in_progress → done：方案（待批准）与核验 ADR 已写好
