---
id: D-0006
title: 两个代理各用自己的 Docker 环境，不共用
status: accepted
date: 2026-10-31
author: claude
supersedes:
---

# D-0006 两个代理各用自己的 Docker 环境，不共用

## 背景
2026-10-31 人类在对话中要求：**claude 和 codex 两边不要共用一个 Docker 环境**。本决策由人类作出，claude 记录。

现状：
- claude 在 Claude Code 云端会话的独立容器里运行自己的 Docker daemon。容器被回收、重启时 Docker 会停，需要重新启动，这和 codex 无关。
- codex 在自己的工作区里工作（AGENTS.md 第 9 节）。

共用的风险：Compose 栈和验收脚本里的名字和端口都是固定的。例如：
- Compose 项目 `my-workspace` 与 `my-workspace-vikunja`
- 入口 `127.0.0.1:8080`
- 模拟 GitHub 容器 `ws-e2e-fake-github`，端口 `18090`

验收流程里还有 `docker compose down -v`、`docker rm -f`、`make vikunja-down`。两边如果连同一个 Docker daemon，会互相停掉、删除对方的容器、网络和数据卷，验收结果也会被对方的数据干扰。

## 决策
1. claude 和 codex 各用自己的 Docker 环境：各自的 Docker daemon，在各自的机器或云端容器里。不连接、不操作对方的 daemon、容器、网络和数据卷，也不用 `DOCKER_HOST` 指向别人的或共享的 daemon。
2. 不在同一个 Docker daemon 上同时运行两边的 Compose 栈、验收脚本或测试数据库。
3. 全栈与浏览器联调由 claude 的验收任务在 claude 自己的环境里运行。codex 验证前端时：
   - 用契约模拟 BFF 或本地 Vite；
   - 需要真实栈时，在 codex 自己的 Docker 环境里另起一套。那套环境的 `.env`、镜像和数据只属于 codex。
4. 只有一台机器、无法分开时，先问人类，不要默认共用。
5. 遇到不认识的容器、网络或数据卷，或端口被占，先确认是不是自己的环境，不要停止或删除不认识的资源。
6. GitHub Actions 的每个 job 都在自己的干净 runner 上运行，不受本决策影响。

## 理由
- 固定的名字和端口让两套栈无法安全地共存在一个 daemon 上。
- 验收要能复现，环境被另一方改动后，结果就不可信。

## 代价 / 后果
- 两边各自承担镜像构建和启动时间。
- codex 想用真实后端联调时，要自己准备 Docker 环境；否则继续用模拟 BFF，真实联调交给 claude 的验收任务（与 Sprint 2 ~ 4 的做法一致）。

## 考虑过的其他方案
- 给每个代理不同的 `COMPOSE_PROJECT_NAME` 和端口，在同一个 daemon 上共存：验收脚本里还有固定的容器名和端口，改起来面广，也容易漏；而且人类明确要求不共用。
