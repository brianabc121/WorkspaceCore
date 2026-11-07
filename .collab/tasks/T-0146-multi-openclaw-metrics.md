---
id: T-0146
title: 多实例 M3 上报与告警：cgroup 与 boot_id、整机去重、每个智能体的样本、服务器级与实例级告警、get_host_status
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs
depends_on: T-0144
created: 2026-11-07
updated: 2026-11-07
---

# T-0146 多实例 M3 上报与告警：cgroup 与 boot_id、整机去重、每个智能体的样本、服务器级与实例级告警、get_host_status

## 目标
整台服务器与每个智能体的负载（方案第 5、9、10 节）。

## 验收标准
- [x] host-report.mjs：读自己容器的 cgroup（CPU、内存、进程数）、数据目录大小、网卡；boot_id 的 HMAC
- [x] 整机数据按服务器 + 分钟去重；每个智能体的样本；手动部署的实例按 boot_id 自动归属服务器（手动指定优先）
- [x] 服务器级与实例级告警；get_host_status 返回整机与自己容器的用量
- [x] 测试与文档

## 背景 / 约束 / 参考
- 方案：`docs/v2/MULTI_OPENCLAW_PLAN.md`（人类 2026-11-07 批准：D5、D6 不考虑旧部署，其余按推荐）
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 03:23 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 03:23 UTC · claude · 人类 2026-11-07 在对话中指派：多实例服务器方案按决定全部落实（D5、D6 不考虑旧部署，其余按推荐）。
- 2026-11-07 04:08 UTC · claude · 开始任务
- 2026-11-07 04:08 UTC · claude · 完成（本 PR）：host-report.mjs 读自己容器的 cgroup v2 / v1（CPU usec、内存已用 = 当前 − inactive_file、进程数）、数据目录大小（每 10 分钟，最多 20 万个文件，不跟随符号链接）、自己网卡、boot_id 的 HMAC（盐按工作空间固定，MYWS_HOST_SALT）；HostService 按指纹归到服务器（机器重启：旧指纹最后一次上报早于新指纹那台机器的开机时间 + 30 秒就当同一台；换到另一台机器、新机器自动识别；手动指定的不改，指纹对不上时不记整机数据；自动识别的服务器空了就删）；整机样本每台每分钟一份、整机网速 = 各实例之和；实例样本；整机告警（每台服务器一份）与实例告警（内存接近上限 10 分钟、一小时重启 3 次、15 分钟占 80% CPU）；get_host_status 多 me（自己的用量，看不到别的实例）。测试：test_host_metrics_api（9 个）、test_host_report_script（5 个）。
- 2026-11-07 04:08 UTC · claude · status in_progress → done：上报与告警完成：cgroup、boot_id 指纹、整机去重、每个智能体的样本、两级告警、get_host_status（PR-A）
