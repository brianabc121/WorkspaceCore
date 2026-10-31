---
id: T-0048
title: 首页后台刷新时组件闪成“正在加载”，导致 Sprint 2 浏览器验收偶发失败
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web,tests/e2e
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0048 首页后台刷新时组件闪成“正在加载”，导致 Sprint 2 浏览器验收偶发失败

## 目标
首页把首页聚合与组件列表的 `isFetching` 当作每个组件的 loading（`DashboardWorkspace.vue`）。
后台重新拉取时（SSE 建立或重连后的失效、窗口重新获得焦点等），所有组件都会短暂变成“正在加载组件…”，已经显示的内容被盖住。
PR #66 的 CI 里，Sprint 2 浏览器验收恰好在这个窗口检查“尚未接入”文字，因此失败：
- 首页聚合在 08:30:36.739 返回；
- SSE 在 .744 建立；
- 失效后在 .775 再次拉取；
- .776 检查失败。

## 验收标准
- [x] 已有数据时，后台重新拉取不再让组件显示“正在加载”，继续显示已有内容；只有没有数据时（首次加载、失败后重试）才显示加载中
- [x] 前端单元测试覆盖：已有数据时重新拉取不显示加载中、没有数据时显示加载中
- [x] `tests/e2e/sprint2_browser.cjs` 对异步出现的文字用等待而不是立即计数
- [x] `apps/web` 的 lint / typecheck / test / build / check:api 通过；Sprint 2 浏览器验收本机通过

## 背景 / 约束 / 参考
- 发现于 PR #66（T-0045）的 `compose` job。按规则在同一个 PR 里修复（本会话只能推送到一个分支），在 PR 里说明。
- D-0007：codex 暂停期间 `apps/web` 由 claude 修改。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 08:32 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 08:33 UTC · claude · 开始任务
- 2026-10-31 08:40 UTC · claude · status in_progress → done：useDashboard 新增 cardLoading：已有数据时后台重新拉取不再让组件显示加载中；新增两项前端测试（去掉修复时第一项失败）；sprint2_browser 改为等两段降级说明都出现再计数；web 全部检查通过，Sprint 2 浏览器验收本机连续 3 次通过，完整冒烟通过
