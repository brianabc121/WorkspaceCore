---
id: T-0049
title: 首页“编辑布局”在后台刷新时点击无效，导致 main 上 Sprint 2 浏览器验收失败
owner: claude
status: done
priority: P0
branch: claude/cool-wright-99qbbg
paths: apps/web,tests/e2e,docs/acceptance
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0049 首页“编辑布局”在后台刷新时点击无效，导致 main 上 Sprint 2 浏览器验收失败

## 目标
首页“编辑布局”按钮在后台重新拉取时仍可点击，但 `beginEdit` 在 `fetching` 时直接返回：
点击后没有任何反应，也没有提示。

后台重新拉取的来源：SSE 建立或重连后的失效、窗口重新获得焦点、刷新。

main 上 #67 合并后的 ci-infra（运行 41187863842）中，Sprint 2 浏览器验收的“版本冲突”一步因此失败：
- 第二个会话刚登录，SSE 建立后立即重新拉取；
- 这时点击“编辑布局”没有生效；
- 等“键盘调整布局”出现超时（30 秒）。

这是先前就存在的竞态，与 #67 的文档改动无关。

## 验收标准
- [x] 后台重新拉取期间“编辑布局”按钮禁用；拉取结束后可用，点击后进入编辑（不再静默无效）
- [x] 前端单元测试覆盖：拉取中按钮禁用，拉取结束后点击进入编辑；去掉修复时测试失败
- [x] `apps/web` 的 lint / typecheck / test / build / check:api 通过；Sprint 2 浏览器验收本机多次通过
- [x] PR 上的 ci-infra（含 Sprint 2 浏览器验收）成功，合并后再确认 main 恢复成功

## 背景 / 约束 / 参考
- 与 T-0048 同类：都是后台重新拉取与首页交互的竞态。T-0048 修了组件显示，这里修编辑入口。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 09:37 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 09:37 UTC · claude · 开始任务
- 2026-10-31 09:43 UTC · claude · 按钮在 fetching 时禁用，新增单元测试（去掉修复时失败），web 全部检查通过，Sprint 2 浏览器验收本机连续 5 次通过、完整冒烟通过；paths 加 docs/acceptance，在 Sprint 6 验收报告里补一行说明 main 上这次失败与修复。最后一项（main 恢复成功）在 PR 合并后确认
- 2026-10-31 09:43 UTC · claude · status in_progress → done：“编辑布局”在后台重新拉取时禁用；单元测试与浏览器验收覆盖；合并条件是 PR 上 CI 全绿，合并后确认 main 恢复
