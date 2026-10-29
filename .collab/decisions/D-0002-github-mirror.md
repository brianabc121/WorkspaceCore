---
id: D-0002
title: GitHub Issues 只作为 .collab/ 的镜像与提醒；对话中的指派由代理代为记录
status: accepted
date: 2026-10-29
author: claude
supersedes:
---

# D-0002 GitHub Issues 只作为 .collab/ 的镜像与提醒；对话中的指派由代理代为记录

## 背景
D-0001 确定以仓库文件为唯一事实来源，交接随 PR 合并生效，但留下了两个缺口：

1. 交接合并后要靠人类记得去通知接收方，进度也只能在命令行或 Actions Summary 里看，GitHub 上看不到；
2. 人类在对话里说“T-0007 交给你了”时，代理没有合规的方式记录这次指派：
   `start` 要求 owner 是自己，而 owner 只有人类能改。

## 决策
1. 新增 `collab-sync` 工作流（`scripts/collab_sync.py`），在 main 更新时和每天定时运行：
   为新交接单开 `collab-handoff` 提醒 issue（附开工话术），接收方接手后自动关闭；
   维护一个 `collab-board` 看板 issue；在看板上标出超过 3 天没更新的进行中任务。
2. 这些 issue **只是镜像**：由 CI 根据 `.collab/` 生成，代理和人类都不手动编辑，任务状态仍只认文件。
3. 人类在本次对话里明确指派任务给代理时，代理先运行
   `collab.py set T-xxxx --as human --owner <自己> -m "人类在对话中指派：…"` 记录，再 `start`。
   这是代理唯一可以用 `--as human` 执行的操作（写入 AGENTS.md 第 3 节）。
4. `collab.py` 本身保持不联网；访问 GitHub API 的代码只放在 `collab_sync.py`，只在 CI 里运行。

## 理由
- 提醒 issue 会触发 GitHub 通知，人类不用盯着仓库；话术直接复制，减少转述出错。
- 看板 issue 用 GitHub 自带的 `GITHUB_TOKEN` 就能维护，不需要额外的令牌或配置。
- 镜像是单向的（文件 → issue），不会出现两处状态互相打架。
- 代为记录指派让日志里留下“人类在对话中指派”的痕迹，比放宽 `start` 的权限检查更容易追溯。

## 代价 / 后果
- 仓库里会多出由 github-actions 创建的 issue。
- 镜像有延迟：只在 main 更新和每天定时运行时刷新。
- `--as human` 有被滥用的风险：靠 AGENTS.md 的规则约束，并在 PR 审查时留意日志里的指派记录。

## 考虑过的其他方案
- **GitHub Projects 看板**：更直观，但用户名下的 Projects v2 需要带 project 权限的个人令牌。
- **在 PR 下评论 @ 接收方代理自动开工**：省掉人类转述，但没有人把关，容易形成循环；暂不采用。
- **允许代理直接 `start` 别人的任务**：最简单，但会让球权规则形同虚设。
