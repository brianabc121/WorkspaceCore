---
id: T-0134
title: 运行服务器方案：一台高配服务器给所有智能体跑容器（模板 / 环境 / 运行、三类容器、回收、管理页面）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs
depends_on:
created: 2026-11-06
updated: 2026-11-06
---

# T-0134 运行服务器方案：一台高配服务器给所有智能体跑容器（模板 / 环境 / 运行、三类容器、回收、管理页面）

## 目标
单独一台高配运行服务器，承担所有智能体的抓取、测试、试运行评估；智能体经工作台工具建和用容器（方案 A：智能体不碰 Docker）。
方案要回答：容器怎么分类、怎么复用、什么时候回收（重建耗时、长时间不用、删除条件），以及管理这台服务器的页面。

## 验收标准
- [x] 调研业内做法并附来源（E2B、Daytona、Coder、OpenHands、GitHub runner、Kubernetes、Modal、Fly、Serverless in the Wild、GreedyDual-Size、gVisor / Kata、Docker）
- [x] `docs/v2/RUNNER_PLAN.md`：模板 / 环境 / 运行三层、三类容器与建容器前必填的问题、生命周期、回收（空闲分档、保留分、删除条件、资源水位、配置记录与快速重建）、三个例子、隔离与安全、runner-agent、MCP 工具、页面、数据模型、与现有规则的关系（例外八的建议写法）、D1 ~ D12、任务拆分 T-0135 ~ T-0141、风险
- [x] `docs/v2/README.md` 加一行

## 背景 / 约束 / 参考
- 人类 2026-11-06 在对话中提出（原文见方案开头），并指定按“方案 A”设计。
- `docs/AGENTS.md` 第 8 节“不得开启 exec 或挂载 Docker socket”：OpenClaw 服务器上不变；在运行服务器上执行命令需要人类新增例外八（方案第 13 节）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 14:59 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 14:59 UTC · claude · 开始任务
- 2026-11-06 14:59 UTC · claude · status in_progress → done：方案（待批准）docs/v2/RUNNER_PLAN.md 已写好：调研附来源；三层、三类容器、回收规则、runner-agent、工具、页面、数据模型；例外八的建议写法与 D1 ~ D12 待人类决定；实施拆为 T-0135 ~ T-0141（批准后再建任务）。
- 2026-11-06 15:00 UTC · claude · 人类 2026-11-06 在对话中指派：按方案 A 设计运行服务器（一台高配服务器跑所有智能体的任务 / 测试；容器分类、复用、回收；管理页面），先调研业内做法再写方案。任务建好时 owner 已是 claude，所以没有另记 set --as human。
