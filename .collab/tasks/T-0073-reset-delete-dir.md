---
id: T-0073
title: 清空时可选删除代码目录（备份与 .env 先移到 /var/backups）
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,docs/deploy-guide.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0073 清空时可选删除代码目录（备份与 .env 先移到 /var/backups）

## 目标
人类要求 `deploy.sh` 清空时可以顺便删除代码目录。代码目录里有本机备份（`backups/`）和 `.env`，不能一起丢掉。

## 验收标准
- [ ] 清空时询问是否同时删除代码目录（默认否）；无人值守用 `--delete-dir`（仍需 `--confirm my-workspace`）
- [ ] 删除前把 `backups/` 与 `.env` 移到 `/var/backups/my-workspace-<时间>/`（700 / 600），完成后说明保留了什么、怎么删
- [ ] 只删除本项目的 git 检出（有 `.git`、`compose.yaml`、`scripts/deploy.sh`），拒绝 `/`、家目录等；按真实路径删除
- [ ] Docker 里已经没有本项目数据时，也能只删除代码目录
- [ ] 沙箱测试：安全检查拒绝、无人值守删除、部署后交互清空并删除代码目录

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 03:40 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 03:40 UTC · claude · 开始任务
- 2026-11-02 03:40 UTC · claude · 人类在对话中要求：清空的时候顺便把代码目录也删掉
