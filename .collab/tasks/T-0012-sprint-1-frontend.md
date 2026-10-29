---
id: T-0012
title: Sprint 1 前端：首次安装、登录登出、路由守卫、工作空间、设置与安全页、审计日志页（S1 前端）
owner: codex
status: todo
priority: P1
branch:
paths: apps/web, .github/workflows/ci-web.yml
depends_on: T-0009
created: 2026-10-29
updated: 2026-10-29
---

# T-0012 Sprint 1 前端：首次安装、登录登出、路由守卫、工作空间、设置与安全页、审计日志页（S1 前端）

## 目标
实现 Sprint 1 的前端：首次安装、登录登出、登录态守卫、当前用户与工作空间、设置与安全页、审计日志页。
只做 `apps/web`；后端由 claude 的 T-0010 / T-0011 实现，开发时按契约用 mock，联调在 T-0013。

## 验收标准
- [ ] 用 `services/api/openapi.json` 生成 TypeScript 类型（npm script），不手抄字段
- [ ] API 客户端：同源相对路径；按契约在写请求上携带 CSRF Token 和工作空间上下文；解析统一错误格式；401 跳转登录、429 给出提示
- [ ] 首次安装页（需要安装时显示）、登录页、登出；未登录访问受保护页面跳转登录
- [ ] 顶栏显示当前用户；有多个工作空间时可切换
- [ ] 设置：个人资料（显示名）、工作空间名称（无权限时只读）、安全设置（修改密码、会话列表与撤销）
- [ ] 审计日志页：只对 owner / admin 显示入口（后端同样会拒绝其他角色）
- [ ] 每个视图具备 loading / empty / error / unauthorized 状态（规范 8.3）；写操作防重复提交
- [ ] Vitest 覆盖登录态守卫、CSRF 头、错误处理；`npm run lint / typecheck / test / build` 通过并把输出写进 PR
- [ ] 顺带处理 PR #7 审查中的非阻塞建议：删除 `apps/web/PR_DESCRIPTION.md`、`apps/web/IMPLEMENTATION_PLAN.md`；`actions/setup-node` 升到 v5；`globals.node` 只用于配置与测试文件

## 背景 / 约束 / 参考
- 依赖：T-0009（接口契约）合并后开始
- 接口契约：`docs/decisions/2026-10-30-sprint-1-auth-contract.md`（T-0009 产出）
- 不要改 `apps/web` 以外的文件（`ci-web.yml` 除外）；不要勾选 `docs/TASKS.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: codex）
