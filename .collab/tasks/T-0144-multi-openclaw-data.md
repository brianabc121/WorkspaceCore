---
id: T-0144
title: 多实例 M1 数据：agent_servers、实例加列、部署表、整机与每个智能体的样本表（迁移 0045）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs
depends_on: T-0143
created: 2026-11-07
updated: 2026-11-07
---

# T-0144 多实例 M1 数据：agent_servers、实例加列、部署表、整机与每个智能体的样本表（迁移 0045）

## 目标
服务器与实例两层的数据结构（方案第 6 节）；人类确认还没有部署过，直接换新表。

## 验收标准
- [ ] 迁移 0045：agent_servers、agent_deployments、实例加列（server_id、server_manual、mem_limit_mb、cpus）、agent_ssh_jobs 加 server_id
- [ ] agent_server_samples、agent_instance_samples 代替 agent_host_samples；agent_host_state 加 cgroup 累计值
- [ ] 升级 / 降级测试；数据字典与接口契约

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
- 2026-11-07 04:08 UTC · claude · 开始任务
- 2026-11-07 04:08 UTC · claude · 迁移 0045（本 PR）：agent_servers（kind auto / ssh、machine_key 部分唯一索引）、agent_server_samples（整机，每台每分钟一份，不含网速）、agent_instance_samples（每个实例，含 server_id）、agent_host_state 加 cpu_usec / machine_key / container、agent_instances 加 server_id / server_manual / mem_limit_mb / cpus（CHECK）；删除 agent_host_samples（D6）。升级 / 降级 / alembic check 在临时库上通过；数据字典与接口契约已更新。下一步（PR-B）：agent_servers 加 SSH 字段、agent_ssh_targets 换成 agent_deployments、agent_ssh_jobs 加 server_id（迁移 0046）。
- 2026-11-07 05:30 UTC · claude · PR-B：迁移 0046（agent_ssh_targets 按服务器、agent_deployments、ssh_jobs 可空实例与 prepare、ops_actions 加 server_id 与 containers）与接口契约、数据字典完成；本地 test_agent_ssh / install / ops / servers 全部通过。
