---
id: T-0137
title: 运行服务器 R2 模板：保存、构建、版本、runner_templates / runner_template_save
owner: claude
status: todo
priority: P1
branch:
paths: services/api,docs
depends_on: T-0136
created: 2026-11-06
updated: 2026-11-06
---

# T-0137 运行服务器 R2 模板：保存、构建、版本、runner_templates / runner_template_save

## 目标
智能体与管理员都能直接定义模板（方案第 3、10 节，D1、D9）。

## 验收标准
- [ ] 模板的保存（版本号加 1）、在运行服务器上构建或拉取、镜像 digest、构建耗时与大小
- [ ] runner_templates / runner_template_save 工具；只能改自己建的

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
