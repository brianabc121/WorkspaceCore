---
id: T-0113
title: 服务器状态：host-report.mjs 与 crontab、上报接口、样本与清理、实例页面板与趋势、列表指示、告警、get_host_status（迁移 0031）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e,scripts
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0113 服务器状态：host-report.mjs 与 crontab、上报接口、样本与清理、实例页面板与趋势、列表指示、告警、get_host_status（迁移 0031）

## 目标
按方案第 5 节：接入包每分钟上报宿主机指标，工作台存 30 天样本，实例页显示当前值与趋势，超阈值告警，智能体能读自己的服务器状态。

## 验收标准
- [ ] 迁移 0031：指标样本与上一次累计值；每天清理 30 天前的样本
- [ ] 接入包 `bin/host-report.mjs`（读 /proc、statfs、cgroup 上限）、CHECKLIST、SSH 部署的 crontab；单元测试
- [ ] `POST /agent-api/v1/host-metrics`（实例凭证、限流、严格校验）；CPU 使用率与网速由两次之差算出
- [ ] 实例页“服务器状态”（当前值、24 小时 / 7 天 / 30 天趋势、表格视图、过期提示）、实例列表指示；告警（磁盘、内存、负载、未上报）
- [ ] `get_host_status` 只读、只看自己；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
