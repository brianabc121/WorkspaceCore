---
id: T-0138
title: 运行服务器 R3 环境与运行：env_* 工具、文件、限额、执行记录、实例运行环境权限（例外八）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs
depends_on: T-0137
created: 2026-11-06
updated: 2026-11-06
---

# T-0138 运行服务器 R3 环境与运行：env_* 工具、文件、限额、执行记录、实例运行环境权限（例外八）

## 目标
智能体经 env_* 工具建、用、停、删环境并执行命令；实例卡片的运行环境权限（方案第 4、5、8、10 节，例外八）。

## 验收标准
- [ ] env_find / env_create（先复用）/ env_exec（同步或运行编号 + 唤醒）/ env_run / env_files / env_setup / env_update / env_stop / env_release
- [ ] 按智能体的选项生成容器参数，宿主保护不变
- [ ] 限额、执行记录、审计、通知管理员；关掉权限后环境全部停止
- [ ] 实例卡片“运行环境权限”：开关、限额、可注入的密钥

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
