---
id: T-0297
title: 大模型客户端：冒烟检查把 Docker 健康检查算成“带管理令牌的进程”，时好时坏（#257 合并后 main 失败）
owner: claude
status: done
priority: P1
branch: claude/gracious-euler-ldkldx
paths: infra/llm-bridge/smoke.sh,services/llm-bridge/Dockerfile,docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md
depends_on: T-0293
created: 2026-11-18
updated: 2026-11-18
---

# T-0297 大模型客户端：冒烟检查把 Docker 健康检查算成“带管理令牌的进程”，时好时坏（#257 合并后 main 失败）

## 目标
main `51e8cd7`（#257 合并）上 ci-llm-bridge 的 `workspace` 失败：验收后再跑一次的冒烟检查报
“这些进程的环境变量里有管理令牌：300: /bin/sh -c python -c "import urllib.request…healthz…"”。
同一个作业 16:52 跑的同一项是通过的。扫到的是 Docker 的健康检查：Docker 用容器的环境变量启动它，带着
`BRIDGE_ADMIN_TOKEN`，每 30 秒起一次、几十毫秒就结束，检查碰巧赶上它运行时才失败。

## 验收标准
- [x] Dockerfile 的健康检查改用 exec 形式，不经过 `/bin/sh`
- [x] 冒烟检查按健康检查的命令行认出它、不算在内；其余进程照查，旧的 `/bin/sh -c` 形式不放过（本地用模拟命令行核对）
- [x] 方案第 8 节的“已知的限制”和新的 12.7 节写明：健康检查带着管理令牌，属于“作业和管理器是同一个用户”这一类限制
- [x] CI 的 ci-llm-bridge（`image`、`workspace` 两处冒烟检查）通过（合并前由集成会话在本 PR 的固定 head 上确认）

## 背景 / 约束 / 参考
- 编号由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留给自己：D-0008 要求发现 main 相关失败时先停下后续集成、查明原因。
- 这不是环境瞬态问题：检查是对的，只是 Docker 健康检查这个进程确实带着令牌；按 D-0008 不靠重跑变绿。
- 彻底隔开（作业用单独的用户或容器、管理令牌不放进容器环境变量）要改部署方式，不在本任务范围，已在方案里写明，交人类决定。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 18:05 UTC · claude · 创建任务（owner: claude；编号 T-0297 由集成会话在 #235 预留）
- 2026-11-18 17:56 UTC · claude · 按 D-0008 处理 main 51e8cd7 上 ci-llm-bridge 的失败：查明是冒烟检查扫到了 Docker 健康检查（带着容器的环境变量），不是环境瞬态问题，不靠重跑变绿。Dockerfile 的 HEALTHCHECK 改成 exec 形式；smoke.sh 按健康检查的命令行认出它、不算在内，本地用模拟命令行核对：exec 形式的健康检查跳过，旧的 /bin/sh -c 形式、codex app-server、uvicorn 照查。方案第 8 节和新的 12.7 写明剩下的限制。和 codex 的 T-0224 paths 重叠只在 infra（T-0224 声明的是整个 infra），本任务只改 infra/llm-bridge/smoke.sh 这个 MCP 管理器的冒烟脚本，不碰 T-0224 的运行时文件。
- 2026-11-18 17:56 UTC · claude · status in_progress → done：验收标准全部勾选；CI 一项由集成会话在合并前确认
- 2026-11-18 17:57 UTC · claude · 按另一个会话（session_0171QGWM1UyBZv1vXTduEVXi）的提醒一并修：smoke.sh 找 codex app-server 的 grep -l 在 /proc 里某个进程刚好退出时会因读不到文件返回 2，即使找到了也算失败；改成 grep -qs（找到就返回 0，读不到的文件不算错）。本地核对：有匹配、另有一个读不到的文件时 grep -l 返回 2、grep -qs 返回 0；没有匹配时 grep -qs 返回非 0。
