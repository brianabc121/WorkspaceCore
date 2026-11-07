---
id: T-0146
title: 多实例 M3 上报与告警：cgroup 与 boot_id、整机去重、每个智能体的样本、服务器级与实例级告警、get_host_status
owner: claude
status: todo
priority: P1
branch:
paths: services/api,docs
depends_on: T-0144
created: 2026-11-07
updated: 2026-11-07
---

# T-0146 多实例 M3 上报与告警：cgroup 与 boot_id、整机去重、每个智能体的样本、服务器级与实例级告警、get_host_status

## 目标
整台服务器与每个智能体的负载（方案第 5、9、10 节）。

## 验收标准
- [ ] host-report.mjs：读自己容器的 cgroup（CPU、内存、进程数）、数据目录大小、网卡；boot_id 的 HMAC
- [ ] 整机数据按服务器 + 分钟去重；每个智能体的样本；手动部署的实例按 boot_id 自动归属服务器（手动指定优先）
- [ ] 服务器级与实例级告警；get_host_status 返回整机与自己容器的用量
- [ ] 测试与文档

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
