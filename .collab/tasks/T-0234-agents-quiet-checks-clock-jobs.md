---
id: T-0234
title: agents 场景 3、10、11：“乙没有调用模型”只数对话里的各轮（OpenClaw 按时钟跑的自动任务不计入）；场景 13 核对后不改
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: tests/e2e/agents_acceptance.py,docs/decisions/2026-11-04-agent-messaging-verification.md
depends_on:
created: 2026-11-14
updated: 2026-11-14
---

# T-0234 agents 场景 3、10、11：“乙没有调用模型”只数对话里的各轮（OpenClaw 按时钟跑的自动任务不计入）；场景 13 核对后不改

## 目标
T-0233（PR #208）查清：OpenClaw 2026.9.36 自己按时钟跑的自动任务（定时心跳、Dreaming、定时任务）会用实例的模型密钥调用模型，
落进“这段时间某个实例没有调用模型”的检查窗口就会偶发失败。T-0233 只改了场景 22；人类 2026-11-14 要求把场景 3、10、11、13 的同类检查也一并处理。

- 场景 3、10、11：意思是“乙在对话里没有响应”，改成只数对话里的各轮（`conversation_turns`），不计入的自动任务写进详情（`quiet_detail`）。
- 场景 13：核对后不改。它的时间起点在 `secrets reload` 之后，之后的任何调用（包括自动任务）都应该用新密钥；
  自动任务还在用旧密钥本身就是问题，排除掉反而放松了判定。加了一条注释说明。

## 验收标准
- [x] 场景 3、10、11 的三项只数对话里的各轮；对话里多一次调用照样失败（用 T-0233 本地实验留下的真实记录核对：只有自动任务 → 通过，加 1 次对话调用 → 失败）。
- [x] 场景 13 的处理写明理由。
- [x] `ci-infra` 的 `agents` 任务通过：以本 PR 的 CI 为准（D-0004：没通过不合并，这一条随合并成立）。

## 背景 / 约束 / 参考
- 依据：`docs/decisions/2026-11-04-agent-messaging-verification.md` 6.2 的 2026-11-14 补充。
- 路径与 T-0224（codex·handoff，codex 暂停中）声明的 `tests/e2e`、`docs/decisions` 重叠；人类在对话里明确要求这次修改。
- 本地没有整套工作台，这三个场景的真实运行以 CI 为准。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-14 11:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-14 11:02 UTC · claude · 开始任务
- 2026-11-14 11:03 UTC · claude · status in_progress → done：场景 3、10、11 改为只数对话里的各轮，场景 13 核对后不改（理由见目标）；合并以 PR 的 CI agents 任务通过为前提
