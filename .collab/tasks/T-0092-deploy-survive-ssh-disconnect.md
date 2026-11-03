---
id: T-0092
title: 一键部署脚本在 SSH 断线后继续运行（自动进入 tmux 会话、重新运行时接回、拉取镜像失败重试）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts,docs,tests,.github/workflows
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0092 一键部署脚本在 SSH 断线后继续运行（自动进入 tmux 会话、重新运行时接回、拉取镜像失败重试）

## 目标
人类反馈：用一键部署脚本部署时，拉取 OpenIM 依赖镜像耗时较长，SSH 断开（`Timeout, server not responding`），
脚本随终端被终止，部署无法完成。改为在终端里运行耗时的动作（部署、升级、接入组件）时自动进入专用的 tmux 会话：
断线后部署继续；重新登录后再运行脚本就接回原来的会话。拉取镜像失败时自动重试。

## 验收标准
- [x] 在终端里运行 install / upgrade / connect 时自动进入 tmux 会话（独立的 `-L my-workspace` 服务，不影响用户自己的 tmux）；没有 tmux 时自动安装，装不上时照常运行并提醒
- [x] SSH 断开（外层终端被杀）后部署继续完成；再运行一次脚本接回同一个会话，不会开始第二次部署
- [x] 脚本结束后停在最后的输出上，按回车才关闭；退出码保留
- [x] 没有终端（CI、管道）、已经在 tmux / screen 里、`WORKSPACE_NO_TMUX=1`、status / reset 时不进入 tmux
- [x] 拉取镜像（core、OpenIM、n8n）失败时重试 3 次；429 toomanyrequests 不重试
- [x] 测试（假的 tmux / make）与 `docs/deploy-guide.md` 说明

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 06:36 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 06:36 UTC · human · owner unassigned → claude：人类在对话中指派：用一键部署脚本部署时耗时长，SSH 会被断开，之后无法部署成功（拉取 OpenIM 依赖镜像时 Timeout, server not responding）
- 2026-11-03 06:36 UTC · claude · 开始任务
- 2026-11-03 06:44 UTC · claude · 完成：deploy.sh 在终端里运行 install / upgrade / connect 时自动进入专用 tmux 会话（-L my-workspace；没有 tmux 时自动安装），SSH 断开后部署继续；再运行一次（菜单之前）就接回同一个会话；结束后停在最后的输出上按回车关闭。拉取镜像（make pull / openim-pull / n8n-pull）失败重试 3 次，429 不重试。验证：真实 tmux + 伪终端模拟断线（外层终端被 kill -9，部署照样跑完，重新运行接回），test_deploy_script.py 28 个通过。
- 2026-11-03 06:44 UTC · claude · status in_progress → done：验收标准全部完成，随 PR 合并生效
