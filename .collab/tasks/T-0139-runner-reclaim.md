---
id: T-0139
title: 运行服务器 R4 回收：空闲分档、删除条件、保留分、水位、快照与重建
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs
depends_on: T-0138
created: 2026-11-06
updated: 2026-11-07
---

# T-0139 运行服务器 R4 回收：空闲分档、删除条件、保留分、水位、快照与重建

## 目标
按方案第 6 节回收：空闲分档、删除条件、保留分、资源水位、快照与重建。

## 验收标准
- [x] 空闲 → 停止 → 归档 → 删除记录，按类别与启动耗时
- [x] 删除条件（任务、Issue、项目、时间、空闲、释放、实例停用）与宽限期、通知
- [x] 资源紧张时按保留分回收；镜像清理
- [x] 常驻环境归档前做加密快照，重建时恢复并重放初始化步骤

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
- 2026-11-06 23:31 UTC · claude · 开始任务
- 2026-11-07 00:08 UTC · claude · 完成：迁移 0043 runner_snapshots；快照分块加密（每份一个数据密钥，AES-256-GCM，凭证密钥封存）存对象存储、/runner-api/v1/snapshots/{id}/parts/{n}；归档、重建（模板镜像被清就先重建 → 恢复快照 → 重放初始化步骤）；删除条件 task_done / issue_closed / project_archived 与实例停用；空闲分档、会话最长存活、保留分、内存与磁盘水位、镜像清理、磁盘告急（工作台拒绝新建 + runner-agent 本地停止一次性运行）；新建时内存不够先按保留分停掉空闲环境；管理员动作 archive / keep / restore、env_update keep；过期快照与记录清理。本地 Docker 实测见核验记录 4.1。偏差：资源不够不排队（返回 RESOURCE_BUSY），当前版本的模板镜像不自动清理（方案 6.2、6.4、17 已改）。
- 2026-11-07 00:10 UTC · claude · status in_progress → done：回收全部完成：快照与重建、删除条件、保留分、水位、镜像清理、宽限期与通知；测试与本地 Docker 实测见核验记录 4.1
