---
id: T-0051
title: Sprint 5 浏览器验收：断开期间 Chrome 报 ERR_SOCKET_NOT_CONNECTED 被误记为页面错误
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e/sprint5_browser.cjs
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0051 Sprint 5 浏览器验收：断开期间 Chrome 报 ERR_SOCKET_NOT_CONNECTED 被误记为页面错误

## 目标
PR #69（只改文档）合并后，main 上 ci-infra 的 Sprint 5 浏览器验收失败（run 41202363148）。
所有功能断言都通过了，失败原因是：A16 故意停掉 Caddy、重启 api 期间，Chrome 报 `Failed to load resource: net::ERR_SOCKET_NOT_CONNECTED`，脚本把它记成了页面错误。
根因有两层：
- 断开期间的白名单里没有这个错误码；
- Chrome 这类消息的文本里不带地址（地址在 `location().url`），白名单里的 `events/stream` 规则也就匹配不上。
同一份代码在 PR 上是通过的，这次失败取决于连接断开的时机。

## 验收标准
- [x] 控制台过滤改为同时匹配消息文本和 `location().url`，并补上 `ERR_SOCKET_NOT_CONNECTED`；只在断开期间放过，其他时候照常记录（并带上地址，便于定位）
- [x] 用脚本里取出的真实过滤函数核对：旧写法会把这条消息记为问题，新写法放过；断开期间真正的脚本错误、非断开期间的连接错误仍被记录
- [x] 用真实 Chromium 确认资源加载失败的消息地址在 `location().url` 里
- [ ] PR 的 ci-infra（含 Sprint 5 浏览器验收）通过，合并后 main 的工作流全部成功

## 背景 / 约束 / 参考
- 失败日志：ci-infra run 41202363148，job 124540516059，第 20 步
- 同类问题：T-0048、T-0049（后台刷新与断开时序导致的偶发失败）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 13:05 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 13:05 UTC · claude · 开始任务
- 2026-10-31 13:06 UTC · claude · 修复已推送到 PR；等 CI 通过并合并后，确认 main 的工作流成功再改为 done
