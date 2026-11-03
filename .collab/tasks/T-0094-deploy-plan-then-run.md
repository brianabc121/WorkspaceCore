---
id: T-0094
title: 一键部署脚本重新设计：先问完再后台运行（系统服务 + 日志）、自动检测网页首次安装、断点续跑
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,services/api/tests/test_deploy_script.py,docs/deploy-guide.md,docs/decisions,Makefile,.github/workflows/ci-infra.yml
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0094 一键部署脚本重新设计：先问完再后台运行（系统服务 + 日志）、自动检测网页首次安装、断点续跑

## 目标
一键部署脚本（T-0092 起在 tmux 里运行）仍然边做边问：断线后部署会停在某个问题上等人接回终端，
网页上的“首次安装”还要回到终端按回车。人类要求重新设计：先在前台把问题一次问完，再在后台无人值守地运行；
管理员仍在网页上创建，脚本自动检测；后台用系统服务 + 日志。

## 验收标准
- [x] 部署 / 升级 / 接入：前台一次问完全部参数与选择（含 n8n、OpenIM、OpenClaw 接入入口、防火墙、`.env`），显示汇总确认，保存为回答文件（600，不含密码）
- [x] 后台用 systemd-run（没有 systemd 时 setsid nohup）运行，不再提问；没有事先回答的问题按默认值并写进日志
- [x] 网页首次安装由后台自动检测（最多 24 小时），安装凭据只显示在终端与 status，不写进日志
- [x] 前台跟随日志，Ctrl+C / 断线不影响；再次运行接着显示同一个任务；status / logs 查看进度、失败原因与待办
- [x] 失败或中断后重新运行：沿用上次的回答；升级固定到前台确认的提交
- [x] 需要密码的情况不带到后台（connect 时前台当场处理，install 时列为待办）
- [x] 测试（含真实进程的端到端）、shellcheck、部署教程、ADR；本机真实 Docker 跑通

## 背景 / 约束 / 参考
- T-0092（tmux）、T-0093（tmux 接回）；ADR `docs/decisions/2026-11-03-deploy-plan-then-run.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 09:07 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 09:07 UTC · human · owner unassigned → claude：人类在对话中指派：按推荐方案重新设计一键部署脚本——管理员仍在网页首次安装里创建、脚本自动检测；后台改用系统服务（systemd-run，没有时 setsid nohup）+ 日志；开头一次问完全部参数与选择，后台不再提问；断点续跑；status 查看进度
- 2026-11-03 09:07 UTC · claude · 开始任务
- 2026-11-03 09:38 UTC · claude · 实现：scripts/deploy.sh 分为前台 plan_*（一次问完，decide/choose 记下回答）与后台 --run（systemd-run 或 setsid nohup，不提问）；.deploy/ 下的 answers.env、state、deploy.log、todo；wait_for_setup_run 自动检测首次安装；follow_run / status / logs；失败后沿用回答；升级固定 UPGRADE_TO；删除 tmux 部分（保留 real_tty 给 bootstrap）。越界：.gitignore 加 .deploy/（新文件，避免 git status 不干净）。测试 27 passed（含真实进程端到端）；shellcheck -S warning 无警告；本机真实 Docker：install 后台 make up / 迁移 / 冒烟通过，connect 后台等首次安装、调用首次安装接口后 4 秒内自动继续并完成。
- 2026-11-03 09:39 UTC · claude · status in_progress → done：重新设计完成并验证；随 PR 合并生效
- 2026-11-03 09:51 UTC · claude · 处理 PR #137 的 Codex 审查（两条 P2）：1) state_set 的读出-修改-替换整个用 flock 加锁（.deploy/state.lock），launch_run 在 systemd-run 之前写 runner，新增并发测试（去掉锁时 3/3 失败）；2) 后台生成的 n8n 密码：接入前写进 .deploy/n8n-credential.txt（600），接入成功后删除，失败或中断时留着并列为待办，再次运行 owner 已存在时待办指向该文件；新增测试（不在接入前写文件时失败）。ADR 2.2/2.4/3 同步。ruff/mypy/29 个部署脚本测试/shellcheck 通过。
