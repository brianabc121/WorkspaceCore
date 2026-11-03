---
id: T-0093
title: 一键部署脚本经 curl | bash 运行时 tmux 接回失败（can't use /dev/tty）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,services/api/tests/test_deploy_script.py,docs/deploy-guide.md
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0093 一键部署脚本经 curl | bash 运行时 tmux 接回失败（can't use /dev/tty）

## 目标
人类用一键命令（`curl … | sudo bash`）部署，选 1 后脚本进入 tmux 时报 `open terminal failed: can't use /dev/tty`，
看不到进度。原因：`bootstrap` 克隆代码后用 `</dev/tty` 重新启动脚本（菜单要读输入），而 tmux 3.x 拒绝以 `/dev/tty`
打开的终端（`server_client_open` 里的检查）；`new-session -d` 已经成功，部署其实在后台的会话里运行，只是接不回去。

## 验收标准
- [x] 用真正的终端设备（`/dev/pts/N`，从标准输入、输出、错误或 `ps -o tty=` 找）重新启动脚本与接回 tmux
- [x] 接回失败时不当作部署失败：提示部署在后台的会话里继续，并给出接回命令（用 sudo 运行时带 sudo）
- [x] 测试：伪终端里 `real_tty` 找到 `/dev/pts/N`；真的 tmux 在标准输入是 `/dev/tty` 时也能接回（没有修复时复现原报错）；
      接回失败时的提示
- [x] 部署教程写明接回命令与“没能自动接回”时怎么办

## 背景 / 约束 / 参考
- T-0092 引入 tmux 包装；`docs/deploy-guide.md` 第 3 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 08:16 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 08:16 UTC · human · owner unassigned → claude：人类在对话中报告：一键部署脚本选 1 部署后，进入 tmux 时报 open terminal failed: can't use /dev/tty（T-0092 的 tmux 包装）；修复
- 2026-11-03 08:16 UTC · claude · 开始任务
- 2026-11-03 08:19 UTC · claude · 在 tmux 3.4 + 伪终端里复现：stdin 是 /dev/tty 时 attach 报 can't use /dev/tty；改用 real_tty 找到的 /dev/pts/N 后接回成功。新增 tmux_attach（失败时提示接回命令、退出码 0）与 attach_command；bootstrap 优先用真正的终端设备。test_deploy_script.py 31 passed
- 2026-11-03 08:19 UTC · claude · status in_progress → done：修复并测试；随 PR 合并生效
