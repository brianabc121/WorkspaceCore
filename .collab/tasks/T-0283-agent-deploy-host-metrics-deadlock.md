---
id: T-0283
title: 修复部署智能体和每分钟主机数据上报之间的数据库死锁（锁实例改用 FOR NO KEY UPDATE）
owner: claude
status: done
priority: P1
branch: claude/gracious-euler-ldkldx
paths: services/api/app/domain/agent_servers.py,services/api/tests/test_host_metrics_api.py
depends_on: T-0146
created: 2026-11-17
updated: 2026-11-17
---

# T-0283 修复部署智能体和每分钟主机数据上报之间的数据库死锁（锁实例改用 FOR NO KEY UPDATE）

## 目标
集成 PR #246 后，main `b94f8a7` 的 ci-infra `agents` 失败（编号在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6945812698）。
根因是数据库死锁，和 #246 无关：

- `POST /api/v1/agents/instances/{id}/deploy`（`agent_servers.py` `deploy()`）先对实例行 `SELECT … FOR UPDATE`，再锁服务器行；
- 同一时刻，智能体服务器每分钟一次的 `POST /agent-api/v1/host-metrics`（`host_metrics.py` `report()`）先更新服务器行，再写 `agent_instance_samples`。这一分钟第一次写入时，外键检查要对实例行加 KEY SHARE 锁，和 `FOR UPDATE` 冲突；
- 两边互相等，Postgres 判定死锁，回滚部署，接口返回 500。验收脚本 `tests/e2e/agent_ssh_acceptance.py` 第 9 节“用旧接口重新部署甲”碰上了。

## 验收标准
- [x] `AgentServerService._instance(lock=True)`（部署、卸载、指定服务器用）改用 `FOR NO KEY UPDATE`：这三个操作之间照样排队，但不再挡住子表写入时的外键检查。服务器行的锁不变。
- [x] 测试：锁住实例行时，这一分钟第一次上报照样写进去，不等也不死锁。修复前这个测试在 15 秒后超时，修复后 4 秒内通过。
- [x] 相关测试照常通过：主机数据、智能体服务器、部署请求、资源放置。

## 说明
- 为什么只改这一处：死锁只在“先锁实例、再锁服务器”的路径上出现。上报改的是服务器行，只对实例行做外键检查；上报很少需要改实例行（换服务器时 `_move`），那时也是先改实例、再改服务器，和部署顺序一致。
- 这三个操作都不删除实例，所以放开 KEY SHARE 不会让子表在删除过程中插进新行。
- 其他地方对实例行的 `FOR UPDATE`（时间轴、运行环境能力、主模型分配）没有看到死锁，加锁顺序也不同，这次不改。

## 进度日志
- 2026-11-17 claude：在 main `b94f8a7` 的 `agents` 日志里查到死锁，编号在 #235 预留后开工。
- 2026-11-17 10:52 UTC · claude · status in_progress → done：锁实例改用 FOR NO KEY UPDATE，加了复现死锁的测试（修复前超时、修复后通过），相关测试 88 passed
