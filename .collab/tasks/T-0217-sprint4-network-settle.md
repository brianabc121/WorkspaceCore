---
id: T-0217
title: Sprint 4 浏览器验收：先起模拟 GitHub 并等网络稳定；不把 ERR_NETWORK_CHANGED 算作页面错误
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: tests/e2e/sprint4_browser.cjs
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0217 Sprint 4 浏览器验收：先起模拟 GitHub 并等网络稳定；不把 ERR_NETWORK_CHANGED 算作页面错误

## 目标
T-0215（PR #188）把模拟 GitHub 的启动挪到了启动浏览器之前，但 PR #189 的 CI（84d2b84）里 Sprint 4 浏览器验收
又报了 `net::ERR_NETWORK_CHANGED`：容器 21:42:18 启动，浏览器随后马上登录、加载页面。两次失败都在容器启动前后几秒内，
推断是宿主机新网卡的地址变化（含 IPv6 重复地址检测）还要持续一两秒，Chromium 在这期间中断了进行中的请求。
本地沙箱（没有 IPv6）用真实 Chromium + Docker 试了两种时序都复现不出来，所以原因是按 CI 上的时间对应推断的。

## 验收标准
- [x] 模拟 GitHub 在脚本最开头、准备数据之前启动；从容器启动到启动浏览器至少 5 秒
- [x] 只把 `net::ERR_NETWORK_CHANGED` 这一条（宿主机网络变化时浏览器中断请求的环境事件，应用本身不会产生）不算作页面错误；
      其他控制台错误照常失败，各步的功能断言不变（与已有的忽略预期 401 同样做法）
- [x] PR 上 CI 全部通过：按 D-0004，最新提交上所有检查成功才会合并，合并即满足

## 背景 / 约束 / 参考
- 失败的运行：https://github.com/brianabc121/WorkspaceCore/actions/runs/42906310567（compose job 130145080809，重跑一次通过）
- 之前一次：https://github.com/brianabc121/WorkspaceCore/actions/runs/42887147690（PR #187）
- 说明见 PR #187、#189 的评论

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 22:40 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 22:40 UTC · claude · 开始任务
- 2026-11-10 22:40 UTC · claude · status in_progress → done：先起模拟 GitHub、至少 5 秒后启动浏览器；只把 net::ERR_NETWORK_CHANGED 不算作页面错误；合并以 PR 上 CI 全部通过为前提
