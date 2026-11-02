---
id: T-0074
title: 一键部署时安装 Python 3.12（与 CI、api 镜像一致；不替换系统 python3）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,Makefile,scripts/backup_common.sh,scripts/backup.sh,scripts/openim_backup.sh,docs/deploy-guide.md,docs/deployment.md,docs/decisions/2026-11-02-host-python-3-12.md,services/api/tests/test_deploy_script.py
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0074 一键部署时安装 Python 3.12（与 CI、api 镜像一致；不替换系统 python3）

## 目标
人类要求一键部署时把服务器的 Python 升级到 3.12。应用跑在镜像里（自带 3.12），服务器上的 Python 只运行仓库脚本
（生成 `.env`、改写 `.env`、备份、性能基准）；系统自带的版本五花八门（Debian 11 是 3.9），人类在 3.9 上遇到过报错。
统一用 3.12，与 CI、api 镜像一致；但不能替换系统自带的 `python3`（apt 等系统工具依赖它）。

## 验收标准
- [x] `deploy.sh` 部署 / 升级时确保有 Python 3.12：已有能用的 `python3.12` 就用；没有就下载锁定版本（python-build-standalone 3.12.16，构建 20261026）并校验 SHA-256，装到 `/opt/my-workspace/python-3.12.16`，链接为 `/usr/local/bin/python3.12`
- [x] 系统自带的 `python3` 不改动；校验失败不装任何东西；不覆盖用户自己的 `/usr/local/bin/python3.12`；本脚本装过的旧版本自动换成锁定版本
- [x] `deploy.sh` 里所有 Python 调用都用选出的解释器；`make env`、`make perf` 与备份脚本优先用 `python3.12`，没有时退回 `python3`
- [x] ADR 记录版本、校验和、来源与核验方法；部署教程与手册说明；测试检查锁定的小版本与 api 镜像一致
- [x] 测试：Debian 11 / 12、Ubuntu 22.04 / 24.04 全新安装，重复运行不再下载，旧版本升级，GitHub 失败改用镜像，校验和不符，已有不能用的文件，已有可用的 3.12；真实下载；沙箱端到端部署 → 升级（含备份）→ 清空

## 背景 / 约束 / 参考
- `docs/decisions/2026-11-02-host-python-3-12.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 05:40 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 05:40 UTC · claude · 开始任务
- 2026-11-02 05:40 UTC · claude · 人类在对话中要求：一键部署时把服务器的 Python 升级到 3.12
- 2026-11-02 05:57 UTC · claude · 容器测试通过（Debian 11/12、Ubuntu 22.04/24.04 全新安装；重复运行、旧版本升级、下载回退、校验和不符、已有不能用的 /usr/local/bin/python3.12、已有可用的 3.12）；真实下载 4 秒装好 3.12.16；沙箱端到端 curl|bash 部署 → 升级（升级前备份用 python3.12 解析）→ 清空删除目录全部通过
- 2026-11-02 05:57 UTC · claude · status in_progress → done：一键部署时并行安装 Python 3.12.16，系统 python3 不变
