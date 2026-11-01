---
id: T-0068
title: make env 在构建前检查 .env（占位符、密钥格式），避免 api 启动失败
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts/init_env.py,services/api/tests/test_init_env.py,docs/deploy-guide.md,docs/deployment.md
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0068 make env 在构建前检查 .env（占位符、密钥格式），避免 api 启动失败

## 目标
人类在服务器上部署时 `make up` 失败：api 不健康，日志是 `CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制`。
`.env` 是事先准备的（多半是 `cp .env.example .env`，占位符没有替换），而 `make env` 对已存在的 `.env` 一律不检查，
要等镜像构建完、api 启动时才报错。让 `make env`（`make up` 会先运行它）在构建前就发现这类问题并说明怎么改。

## 验收标准
- [x] `make env` 每次都检查 `.env`：占位符（含 `DATABASE_URL` 里的 `:CHANGE_ME@`）、`CREDENTIAL_ENCRYPTION_KEY` 与旧密钥不是 64 位十六进制、生产环境 `SESSION_SECRET` 不到 32 个字符、`OPENIM_CALLBACK_PATH_TOKEN` 太短、Windows 换行
- [x] 有问题时列出问题与处理方法、以非零状态退出（`make up` 不再构建）；不回显密钥；不改写已有的值
- [x] `make env` 生成的 `.env` 与正常的重复运行不受影响，缺少的变量照常补上
- [x] 单元测试 `services/api/tests/test_init_env.py`
- [x] 部署教程第 4 步与常见问题、运行手册的 `make env` 说明更新

## 背景 / 约束 / 参考
- 检查与 API 的启动校验一致：`app/core/config.py`（SESSION_SECRET、回调口令）、`app/domain/credentials.py`（加密密钥）
- 不自动替换已存在的占位符：PostgreSQL 等数据卷可能已经用占位符密码初始化，静默改密码会连不上

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 22:38 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 22:38 UTC · claude · 开始任务
- 2026-11-01 22:38 UTC · claude · 人类在对话中贴出服务器部署日志：make up 时 api 不健康，日志为 CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制；.env 是事先准备的（make env 输出“已存在且变量齐全”），make env 不检查已有值。要求解决
- 2026-11-01 22:40 UTC · claude · init_env.py 增加 problems() 检查与非零退出；测试 10 个通过；ruff/mypy 通过；部署教程第 4 步与常见问题更新
- 2026-11-01 22:40 UTC · claude · status in_progress → done：make env 在构建前检查 .env，问题列出处理方法；测试与文档完成
