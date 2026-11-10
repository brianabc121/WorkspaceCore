---
id: T-0215
title: Sprint 4 浏览器验收先起模拟 GitHub 再启动浏览器（ERR_NETWORK_CHANGED）
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/sprint4_browser.cjs
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0215 Sprint 4 浏览器验收先起模拟 GitHub 再启动浏览器（ERR_NETWORK_CHANGED）

## 目标
PR #187 的 CI（4930e8f，只改了任务文件）里 `ci-infra / compose` 的 Sprint 4 浏览器验收偶发失败：
前面各步都通过，最后汇总页面错误时有一条 `Failed to load resource: net::ERR_NETWORK_CHANGED`。

原因（最可能）：`tests/e2e/sprint4_browser.cjs` 在浏览器已经登录、页面打开以后才 `docker run` 模拟 GitHub 并
`docker network connect`，宿主机上新建网卡；Chromium 检测到网络变化，把进行中的请求（事件流、轮询）以
`ERR_NETWORK_CHANGED` 中断，脚本把所有控制台错误都算作页面错误。日志里没有这条错误的时间，所以只能说时间对得上、机制一致。

## 验收标准
- [x] 启动浏览器之前生成 Token、启动模拟 GitHub 并等它就绪；连接 GitHub 那一步不再启动容器，其余步骤与断言不变
- [x] 不放宽页面错误的判定（仍然把控制台错误算作失败）
- [ ] PR 上 CI 全部通过

## 背景 / 约束 / 参考
- 失败的运行：https://github.com/brianabc121/WorkspaceCore/actions/runs/42887147690（compose job 130078356989），重跑一次通过
- 说明见 PR #187 的评论

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 20:43 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 20:43 UTC · claude · 开始任务
