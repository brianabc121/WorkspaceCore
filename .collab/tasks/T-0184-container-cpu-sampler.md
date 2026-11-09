---
id: T-0184
title: 容器服务器（一）：runner-agent 真实 CPU 差分采样与增量上报
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/runner_agent,services/api/app/schemas/runner.py,services/api/tests/test_runner_cpu_metrics.py,services/api/tests/test_runner_agent.py,services/api/openapi.json,apps/web/src/services/schema.d.ts,docs/decisions,docs/v2/container-server-redesign
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0184 容器服务器（一）：runner-agent 真实 CPU 差分采样与增量上报

## 目标
让容器服务器能显示真实 CPU 用量：runner-agent 在原有每分钟上报里按同一容器两次累计 CPU 时间差分出平均核数，旧 agent 不带也能对账。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 1）

## 验收标准
- [ ] `runner_agent/metrics.py`：`CPUSample`、`ContainerCPUSampler.sample/prune`，按 Docker ID + 启动代次（State.StartedAt）隔离基线；首样本、重启、回退、缺失、窗口 <1 秒或 >180 秒按契约返回未知或重置，有效零增量返回 0
- [ ] 契约数值测试：10e9/100e9 → 11.2e9/102e9 得 0.6 核 / 2 秒；6e9 增量 / 2 秒得 3 核
- [ ] `RunnerReportContainer` 增加 `cpu_used_cores` / `cpu_window_seconds`（同时有效或同时为空，有限、非负、窗口 1–180），旧报告照常解析
- [ ] report 里单个容器 stats 失败只让该容器 CPU / 内存未知，容器条目不丢；卷 Size=-1 或缺失上报为未知，真实 0 仍为 0
- [ ] ADR 记录 Docker 字段与单位核验；OpenAPI 与 schema.d.ts 重新生成

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 09:33 UTC · claude · 开始任务
- 2026-11-09 09:38 UTC · claude · runner-agent CPU 差分采样（metrics.py）、上报字段与校验、单容器读数失败不丢条目、卷 Size=-1 为未知；ADR 2026-11-09-runner-container-metrics；真实 Docker 实测忙 0.985 核 / 闲 0.0；test_runner_cpu_metrics + test_runner_agent 37 passed
