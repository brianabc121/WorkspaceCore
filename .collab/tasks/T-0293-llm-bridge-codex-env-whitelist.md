---
id: T-0293
title: 大模型客户端：Codex app-server 的环境变量改用白名单，不带管理令牌（T-0292 合并后的遗留）
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge/bridge/drivers,services/llm-bridge/tests/test_drivers.py,infra/llm-bridge/smoke.sh,docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md
depends_on: T-0292
created: 2026-11-18
updated: 2026-11-18
---

# T-0293 大模型客户端：Codex app-server 的环境变量改用白名单，不带管理令牌（T-0292 合并后的遗留）

## 目标
集成会话合并 PR #255（T-0291、T-0292）后在 #235 指出：`services/llm-bridge/bridge/drivers/codex.py` 的 `_ensure` 启动 `codex app-server` 时传的是完整的 `os.environ`，里面有 `BRIDGE_ADMIN_TOKEN`。
app-server 没有设成不可转储，同一个用户的作业能从 `/proc/<pid>/environ` 读到管理令牌；Codex 在“可以执行命令”档位跑的命令也继承这份环境。
T-0292 写的“作业读不到管理令牌”要补齐。

## 验收标准
- [x] 官方程序（Claude、CodeBuddy、Codex）共用一份环境变量白名单，不带管理器的 `BRIDGE_*`；Codex 另外只加 `CODEX_HOME`、`HOME`
- [x] 单元测试记下 Codex、Claude 实际收到的环境变量，确认没有 `BRIDGE_*`（在旧代码上失败）
- [x] 冒烟检查：先启动真实的 codex app-server，再查容器里每个进程的环境变量都没有管理令牌
- [x] 方案第 8 节、12.6 节写明
- [x] 管理器 ruff / mypy / pytest

## 背景 / 约束 / 参考
- 编号由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留，owner claude，给 #255 的作者会话。是 T-0292（人类 2026-11-18 在对话中指派）的遗留修复。
- 方案第 8 节已写明“作业和管理器是同一个用户、能读数据卷”，这一条不算阻塞。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 09:58 UTC · claude · 创建任务（owner: claude；编号 T-0293 由集成会话在 #235 预留，并指定给 #255 的作者会话）
- 2026-11-18 09:58 UTC · claude · 开始任务
- 2026-11-18 09:59 UTC · claude · codex.py 的 app-server 改用和 Claude / CodeBuddy 同一份环境变量白名单（drivers/base.py 的 PROGRAM_ENV_PASS / program_env），只另加 CODEX_HOME、HOME，不再带 BRIDGE_*。新测试 test_official_programs_never_get_the_bridge_settings 记下 Codex、Claude 实际收到的环境变量，在旧 codex.py 上失败、新代码通过；冒烟检查先列 Codex 模型（启动真实 app-server）再查所有进程的环境变量没有非空的管理令牌（本机模拟过检查逻辑）。本机：管理器 ruff / mypy 通过，pytest 151 passed / 4 skipped。镜像冒烟以 CI 为准。
- 2026-11-18 09:59 UTC · claude · status in_progress → done：验收标准全部勾选
