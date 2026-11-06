---
id: T-0135
title: 运行服务器 R0 核验：Docker Engine 29.9 接口与行为、gVisor release-20261019.0 安装与实测
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs,services/api/app/runner_agent,services/api/tests
depends_on: T-0134
created: 2026-11-06
updated: 2026-11-06
---

# T-0135 运行服务器 R0 核验：Docker Engine 29.9 接口与行为、gVisor release-20261019.0 安装与实测

## 目标
在锁定版本上核验运行服务器要用的 Docker Engine 接口与 gVisor 的安装、运行行为，不按名称猜（方案第 9.1 节）。

## 验收标准
- [ ] Docker Engine 29.9 的接口与行为本地实测（网络、数据卷、助手容器存取文件、exec、资源与安全参数、stats、system df、kvm 设备报错）
- [ ] gVisor release-20261019.0 的安装方式按官方文档、发布包 sha512 记下
- [ ] gVisor 下的行为在 CI 实测并补进核验记录（随 T-0141）
- [ ] 核验记录 docs/decisions/2026-11-06-runner-verification.md

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
- 2026-11-06 22:15 UTC · claude · 开始任务
