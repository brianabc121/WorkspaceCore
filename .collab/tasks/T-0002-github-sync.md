---
id: T-0002
title: 协作自动化：GitHub 交接提醒、看板 issue、停滞提醒
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts/collab.py, scripts/collab_sync.py, .github/workflows, AGENTS.md, .collab/README.md, README.md
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0002 协作自动化：GitHub 交接提醒、看板 issue、停滞提醒

## 目标
补上 T-0001 留下的缺口：交接合并后自动提醒接收方、在 GitHub 上能直接看到进度、
及时发现停滞的任务；并让人类在对话里的指派有合规的记录方式。设计见 D-0002。

## 验收标准
- [x] `scripts/collab_sync.py`：新交接单 → 提醒 issue（附开工话术）；接手后自动评论并关闭；维护看板 issue
- [x] `.github/workflows/collab-sync.yml`：main 更新时 + 每天北京时间 09:17 运行
- [x] PR 上的 `collab` 检查预览合并后会做的同步（dry-run）
- [x] 看板 / inbox 对超过 3 天没更新的进行中任务标 ⚠
- [x] AGENTS.md：issue 只是镜像（第 1 节）；对话中指派的记录方式（第 3 节）
- [x] `.collab/README.md` 更新自动化说明和建议的 GitHub 设置
- [x] 本地用模拟的 GitHub API 验证：创建提醒 → 重跑不重复 → 接手后自动关闭

## 背景 / 约束 / 参考
- 决策：`.collab/decisions/D-0002-github-mirror.md`（proposed，等人类批准）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:30 UTC · claude · 创建任务（owner: claude）
- 2026-10-29 21:30 UTC · claude · 开始任务：应人类要求（对话中：“给我全部落实”）实现 .collab/README.md 里列出的自动化
- 2026-10-29 21:31 UTC · claude · status in_progress → done：验收标准已全部满足；合并后 collab-sync 首次运行的结果待核对
