---
id: T-0150
title: 时间轴浏览器验收：暂停后的状态改为精确匹配（main 上 ci-infra compose 偶发失败）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e/timeline_browser.cjs
depends_on:
created: 2026-11-07
updated: 2026-11-07
---

# T-0150 时间轴浏览器验收：暂停后的状态改为精确匹配（main 上 ci-infra compose 偶发失败）

## 目标
main 上 f339233（PR brianabc121/WorkspaceCore#169 合并）的 ci-infra compose 任务在“时间轴浏览器验收”失败：
暂停后 `detail(page).getByText('已暂停')` 同时匹配状态提示 `<p role="status">已暂停</p>` 和执行片段那一行
“16:57–16:57 · 已暂停”，Playwright 严格模式报错。以前片段那一行渲染得晚、只匹配到一个，所以是偶发。
改为精确匹配状态提示，不依赖渲染先后。

## 验收标准
- [ ] 暂停一步只匹配状态提示（精确文字），片段那一行同时出现也不报错
- [ ] ci-infra compose 任务通过

## 背景 / 约束 / 参考
测试来自 T-0106（已完成）；PR brianabc121/WorkspaceCore#169 没有改这个文件。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 09:32 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 09:32 UTC · claude · 开始任务
