---
id: T-0031
title: Sprint 4 前端（二）：GitHub 连接设置与开发者页、首页 GitHubActivity 组件、全局搜索 UI（S4-04、S4-05 前端）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web,.github/workflows/ci-web.yml
depends_on: T-0026
created: 2026-10-30
updated: 2026-10-31
---

# T-0031 Sprint 4 前端（二）：GitHub 连接设置与开发者页、首页 GitHubActivity 组件、全局搜索 UI（S4-04、S4-05 前端）

## 目标
按 T-0026 的契约实现 GitHub 与全局搜索的前端：设置页连接 GitHub、`/developer` 开发者页、首页 GitHubActivity 组件，
并把 Sprint 2 的本地搜索升级为跨资源搜索。只做 `apps/web`；后端由 claude 的 T-0028 / T-0029 实现。

## 验收标准
- [ ] S4-04：集成设置页支持 GitHub（Token 只写不回显；测试连接逐项显示检查结果；手动同步；连接状态）；非 admin 只读或隐藏
- [ ] S4-04：`/developer` 页：仓库、Issue、PR、Actions 最近运行（失败的运行显示故障摘要）；分页或加载更多；“在 GitHub 中打开”；限流时明确显示“GitHub 限流、预计何时恢复”，不显示成空列表（A17）；Token 失效时提示重新连接（A13）
- [ ] 首页 GitHubActivity 组件接真实数据，正确展示 ok / stale / unavailable / unauthorized
- [ ] S4-05：全局搜索（顶栏与 `Ctrl/Cmd+K`）调用 `/api/v1/search`：按资源类型分组，显示来源与更新时间，键盘导航，跳到工作台页面或外部原生界面；加载、空结果、错误状态；切换工作空间后不残留上一个工作空间的结果
- [ ] 顺带处理 PR #33 的 3 条非阻塞审查建议（同在集成设置页）：测试结果显示错误码与原因；显示已保存的服务地址与浏览器访问地址；`public_url` 可以恢复为默认（后端已支持显式传 `null`）
- [ ] Vitest 覆盖限流 / Token 失效状态、搜索的工作空间隔离与键盘操作；`npm run lint / typecheck / test / build` 与 `npm run check:api` 通过并把输出写进 PR；不修改 `src/services/schema.d.ts`（D-0005）

## 背景 / 约束 / 参考
- 依赖：T-0026（契约）合并后开始；开发时可用 mock，真实联调在 T-0032
- 规范：第 7.3 节、第 8.1 ~ 8.3 节（`Ctrl/Cmd+K` 搜索、页面状态）；验收场景 A11、A13、A17
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`；需要改契约时在任务日志写明，由 claude 修改（D-0005）
- 与 T-0030 同属 codex，先做哪个都可以，两个任务分别提 PR

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: codex）
- 2026-10-31 00:06 UTC · claude · 提醒 codex：人类在 2026-10-31 的对话中决定两个代理不共用 Docker 环境，见 .collab/decisions/D-0006-separate-docker.md。前端验证继续用契约模拟 BFF / 本地 Vite；需要真实栈时在你自己的 Docker 环境里另起一套，不要连接 claude 的 Docker，也不要和 claude 共用一个 daemon。真实后端的浏览器联调由 claude 的 T-0032 负责。
