---
id: T-0070
title: 部署文档：数据库密码与 .env 不一致时的同步命令
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: docs/deploy-guide.md,docs/deployment.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0070 部署文档：数据库密码与 .env 不一致时的同步命令

## 目标
人类在服务器上按 T-0068 的提示重新生成 `.env` 后，`make migrate` 报 `password authentication failed for user "workspace"`：
PostgreSQL 数据卷已经用旧密码（`CHANGE_ME`）初始化。文档只写了“要在数据库里改密码”，没有给命令。

## 验收标准
- [x] 部署教程常见问题与运行手册给出可直接执行的同步命令（数据不受影响、不需要旧密码、密码不出现在命令行参数里）
- [x] 部署教程第 6 步的 HTTPS 检查命令改为 GET（原来的 `curl -I` 是 HEAD 请求，`/healthz` 返回 405，人类在服务器上实际遇到）
- [x] 命令在同一 PostgreSQL 镜像上验证：先用旧密码初始化数据卷、换新密码重建容器后网络登录失败；执行命令后新密码登录成功、旧密码失败

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 02:13 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 02:13 UTC · claude · 开始任务
- 2026-11-02 02:13 UTC · claude · 人类在对话中贴出 make migrate 的 password authentication failed；同一镜像上复现并验证同步命令；写入部署教程与运行手册
- 2026-11-02 02:13 UTC · claude · status in_progress → done：文档给出数据库密码同步命令，已在同一镜像上验证
- 2026-11-02 02:20 UTC · claude · 人类按教程用 curl -sI 检查 HTTPS 得到 HTTP/2 405：HEAD 请求，/healthz 只接受 GET；本地确认 HEAD→405、GET→200，教程改为 curl -sS -o /dev/null -w '%{http_code}'
