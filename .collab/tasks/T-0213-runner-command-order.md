---
id: T-0213
title: 运行服务器指令按写入顺序下发（agents 场景 26 在 main 上偶发失败）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/models/runner.py,services/api/app/domain/runner_hosts.py,services/api/migrations/versions/,services/api/tests/test_runner_command_order.py,tests/e2e/agents_acceptance.py
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0213 运行服务器指令按写入顺序下发（agents 场景 26 在 main 上偶发失败）

## 目标
PR #185 合并后，main 上 ci-infra 的 agents 任务失败两项（同一份代码在 PR 上跑了两次都通过，#185 没有改运行服务器或 IM）：

1. 场景 26：管理员归档常驻环境后 env_exec 返回“容器不存在”，环境停在 creating。
   根因：用到归档的环境时，工作台在同一个事务里先写“重建”（create_env）、再写“执行”（exec）；
   `runner_commands.created_at` 的默认值 now() 是事务开始的时间，两条完全相同，下发（RunnerHosts.take）只按
   created_at 排序，先后由行在表里的位置决定。exec 先发到 runner-agent 时，它前面没有同一环境的生命周期指令，
   不会等重建，直接在容器重建之前执行。这是 main 上已有的问题，生产中同样会发生（读写文件 env_files 也一样）。
2. 场景 20 的 V3（补丁放回的 5002 系统事件下一轮看得到）：模型调用记录里没有任何“[工作台]”行。
   CI 失败时只输出了实例最后 80 行日志（场景 27 之后），看不到场景 20 那一段，根因暂时无法确定。

## 验收标准
- [x] runner_commands 加写入顺序 seq（迁移 0059，只加列：自增、写入时由数据库生成），下发按 created_at、seq 排序
- [x] 回归测试 tests/test_runner_command_order.py：同一个事务写入、物理位置相反时仍先下发 create_env；
      去掉排序修正时复现为 ['exec', 'create_env']；经 env_exec 的完整路径重建在前、命令照常跑完
- [x] 迁移：空库 upgrade → alembic check → downgrade 0058 → upgrade → check 通过
- [x] 场景 20 的 V3 失败时，检查结果里带上实例日志中插件的发送失败 / 补丁记录与最近几次模型调用，下次能定位
- [x] PR 上 CI 全部通过（含 agents 场景 26）：ce32149 上 12 项检查全部成功

## 背景 / 约束 / 参考
- 失败的运行：https://github.com/brianabc121/WorkspaceCore/actions/runs/42849008610（agents job 129944189371）
- runner-agent 的排队规则：`app/runner_agent/agent.py` 的 `_wait_turn`（同一环境里更早下发的生命周期指令做完后才执行）
- 类似的历史问题：T-0183 日志（Runner.apply_result 读环境不加锁）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 16:57 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 16:57 UTC · claude · 开始任务
- 2026-11-10 16:57 UTC · claude · 根因与修复见目标一节；本地：新测试 2 passed（去掉排序修正时复现为 ['exec', 'create_env']）、test_runner_*.py 与迁移测试 96 passed、迁移空库升降级与 alembic check 通过、ruff / mypy（app migrations tests scripts）/ openapi 检查通过。main 上失败的 agents 任务已按规则重跑一次确认偶发。场景 20 的 V3 根因未定，只加了失败时的诊断信息
- 2026-11-10 17:41 UTC · claude · status in_progress → done：PR #186 的 ce32149 上 12 项检查全部成功（agents 场景 26 通过）；main 上 e5e0956 的 agents 重跑一次也通过，确认 #185 合并后的两项是偶发。场景 20 的 V3 根因待下次失败时按新加的诊断定位
