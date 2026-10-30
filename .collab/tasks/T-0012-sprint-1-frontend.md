---
id: T-0012
title: Sprint 1 前端：首次安装、登录登出、路由守卫、工作空间、设置与安全页、审计日志页（S1 前端）
owner: codex
status: done
priority: P1
branch: codex/T-0012-sprint-1-frontend
paths: apps/web, .github/workflows/ci-web.yml
depends_on: T-0009
created: 2026-10-29
updated: 2026-10-30
---

# T-0012 Sprint 1 前端：首次安装、登录登出、路由守卫、工作空间、设置与安全页、审计日志页（S1 前端）

## 目标
实现 Sprint 1 的前端：首次安装、登录登出、登录态守卫、当前用户与工作空间、设置与安全页、审计日志页。
只做 `apps/web`；后端由 claude 的 T-0010 / T-0011 实现，开发时按契约用 mock，联调在 T-0013。

## 验收标准
- [x] 用 `services/api/openapi.json` 生成 TypeScript 类型（npm script），不手抄字段
- [x] API 客户端：同源相对路径；按契约在写请求上携带 CSRF Token 和工作空间上下文；解析统一错误格式；401 跳转登录、429 给出提示
- [x] 首次安装页（需要安装时显示）、登录页、登出；未登录访问受保护页面跳转登录
- [x] 顶栏显示当前用户；有多个工作空间时可切换
- [x] 设置：个人资料（显示名）、工作空间名称（无权限时只读）、安全设置（修改密码、会话列表与撤销）
- [x] 审计日志页：只对 owner / admin 显示入口（后端同样会拒绝其他角色）
- [x] 每个视图具备 loading / empty / error / unauthorized 状态（规范 8.3）；写操作防重复提交
- [x] Vitest 覆盖登录态守卫、CSRF 头、错误处理；`npm run lint / typecheck / test / build` 通过并把输出写进 PR
- [x] 顺带处理 PR #7 审查中的非阻塞建议：删除 `apps/web/PR_DESCRIPTION.md`、`apps/web/IMPLEMENTATION_PLAN.md`；`actions/setup-node` 升到 v5；`globals.node` 只用于配置与测试文件

## 背景 / 约束 / 参考
- 依赖：T-0009（接口契约）合并后开始
- 接口契约：`docs/decisions/2026-10-30-sprint-1-auth-contract.md`（T-0009 产出）
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: codex）
- 2026-10-30 02:08 UTC · codex · 开始任务
- 2026-10-30 02:08 UTC · codex · 人类在当前对话明确要求执行 #15：从最新 main 开发，限 apps/web、ci-web.yml 和本任务文件，Node 22/npm ci，验证后开 PR，不合并。计划：1 类型生成/API 客户端及测试；2 认证守卫/顶栏/安装登录；3 设置/安全/审计及状态测试；4 完整验证、任务收工、PR。沿用现有 Vue/Pinia/Query 框架与主题，不增加设计审批文件。
- 2026-10-30 02:24 UTC · codex · 完成类型生成/API、认证守卫、工作空间切换、设置/密码/会话/审计及页面状态。Node22 下 50 项测试通过；Chromium 桌面/移动 API mock 冒烟通过。独立审查发现改密码时会话版本需递增，已先复现旧401清空新会话，再在 auth.changePassword 中修复。真实后端联调留 T-0013。设计沿用既有 Vue/Pinia/Query 与表单栈，新增依赖/来源在 apps/web/README.md 记录，遵守本次只改指定路径的要求，不新增范围外 ADR。
- 2026-10-30 02:25 UTC · codex · status in_progress → done：全部验收标准完成。Node v22.23.4 npm ci 后 lint/typecheck/test（50/50）/build 和 check:api 通过；Chromium API mock 桌面/移动冒烟通过；独立审查的密码换发会话版本问题已按 RED→GREEN 修复。实际输出写入 PR，真实联调交 T-0013；Codex 只开 PR，不合并。
