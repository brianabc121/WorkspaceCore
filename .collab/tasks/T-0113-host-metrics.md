---
id: T-0113
title: 服务器状态：host-report.mjs 与 crontab、上报接口、样本与清理、实例页面板与趋势、列表指示、告警、get_host_status（迁移 0031）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,scripts
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0113 服务器状态：host-report.mjs 与 crontab、上报接口、样本与清理、实例页面板与趋势、列表指示、告警、get_host_status（迁移 0031）

## 目标
按方案第 5 节：接入包每分钟上报宿主机指标，工作台存 30 天样本，实例页显示当前值与趋势，超阈值告警，智能体能读自己的服务器状态。

## 验收标准
- [x] 迁移 0031：指标样本与上一次累计值；每天清理 30 天前的样本
- [x] 接入包 `bin/host-report.mjs`（读 /proc、statfs、cgroup 上限）、CHECKLIST、SSH 部署的 crontab；单元测试
- [x] `POST /agent-api/v1/host-metrics`（实例凭证、限流、严格校验）；CPU 使用率与网速由两次之差算出
- [x] 实例页“服务器状态”（当前值、24 小时 / 7 天 / 30 天趋势、表格视图、过期提示）、实例列表指示；告警（磁盘、内存、负载、未上报）
- [x] `get_host_status` 只读、只看自己；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 11:03 UTC · claude · 开始任务
- 2026-11-04 11:03 UTC · claude · 后端：迁移 0031（agent_host_samples 每分钟样本、agent_host_state 上一次累计值 / 当前值 / 告警）；接入包 bin/host-report.mjs（/proc、statfs、cgroup 上限，只报数字）与 crontab 第 4 行、CHECKLIST、SSH 部署；POST /agent-api/v1/host-metrics（实例凭证、每分钟 2 次、严格校验），CPU 使用率与网速由两次之差算出、计数器变小记为空；GET /agents/host-summary、/agents/instances/{id}/host?range=；Worker 每天清理 30 天前的样本、每分钟检查告警（磁盘 <10%、内存 <5%、15 分钟负载持续超过 2 倍核心数、在线却 30 分钟没上报；恢复前只提醒一次）；只读工具 get_host_status 只看自己。前端：实例卡片的 CPU / 内存 / 磁盘小指示（超阈值标红并写出原因），展开后当前值卡片与 24 小时 / 7 天 / 30 天每小时趋势（一张图一条轴：CPU 使用率与负载分两张图，偏离方案“负载与 CPU 使用率一张图”是为了不用双轴；悬停 / 键盘提示、表格视图、缺数据断开），亮暗主题与 390 宽度浏览器检查无溢出、无报错。
- 2026-11-04 11:03 UTC · claude · 检查：ruff / format / mypy（286 个文件）通过；pytest 主机指标 5 项、上报脚本 2 项、契约、SSH 安装共 85 项通过；web lint / typecheck / vitest（43 个文件 424 项）/ build / check:api 通过。真实 OpenClaw 上报留给 T-0115 的 agents CI。
- 2026-11-04 11:04 UTC · claude · status in_progress → done：服务器状态完成：接入包上报、30 天样本与清理、实例页当前值与趋势、列表指示、告警、get_host_status；真实 OpenClaw 上报在 T-0115 的 agents CI 验收
