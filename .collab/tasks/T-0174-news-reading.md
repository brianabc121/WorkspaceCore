---
id: T-0174
title: 二批四页（三）：早报阅读页改造（真实正文与来源、关注主题、本期概况、往期），刷新不生成
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/modules/news,apps/web/tests,tests/e2e
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0174 二批四页（三）：早报阅读页改造（真实正文与来源、关注主题、本期概况、往期），刷新不生成

## 目标
早报改成阅读页：主阅读区 + 侧栏（关注主题、当前已加载条目概况、往期入口），不新增生成流程，不编造“3 件事”或“重复报道合并”。
方案见 `docs/v2/pages-redesign-2/`（Task 3）。早报仍是插拔模块，只改 `apps/web/src/modules/news`。

## 验收标准
- [x] 今天标签重排标题、日期、正文、真实来源与相关资讯；保留往期 / 热榜 / RSS / 搜索 / 收藏六个标签、分页与 `?briefing=id`
- [x] 没有三段结构就显示真实正文；无对应 URL 的段落没有反馈按钮；receiving 保留片段但不称完成
- [x] 侧栏只用当天响应与已加载条目，标明“当前已加载”；不读管理员 settings（owner 才有管理入口）
- [x] 刷新只发 GET，不生成、不推送；模块停用后无请求
- [x] `news-reading-redesign.test.ts` 与 `news-pages.test.ts` 通过；安全渲染拦截脚本、恶意链接与远程图片

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
- 2026-11-09 05:33 UTC · claude · status in_progress → done：早报阅读页（真实分节、关注主题、当前已加载概况、往期）；news-reading-redesign 测试与新闻早报浏览器验收通过
