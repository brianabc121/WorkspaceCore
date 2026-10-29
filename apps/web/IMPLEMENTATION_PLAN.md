# T-0004 前端工程骨架实施计划

依据：docs/DEVELOPMENT_SPEC.md 第 8 节、docs/decisions/2026-10-29-sprint-0-contracts.md。
范围：S0-02；不实现业务、Dockerfile 或后端，不修改 Sprint 总验收状态。

- [x] 配置 npm、Vite、TypeScript、ESLint、Vitest，固定依赖并提交锁文件。
- [x] 先写布局、全部路由、未知路径、主题持久化与存储不可用的行为测试，确认缺失行为失败。
- [x] 实现 src/app/router.ts（history 路由）、src/app/navigation.ts（入口）、App.vue（响应式布局）、首页与通用占位页、stores/theme.ts（亮暗主题）。
- [x] 接入 Pinia、Vue Query 与 Tailwind，代理 /api、/healthz、/readyz 到 localhost:8000。
- [x] 添加 ci-web.yml、前端 README 和版本 ADR。
- [x] 执行 npm ci、lint、typecheck、test、build，并通过本地 HTTP 验证开发服务器和 history 回退。
- [ ] 记录验收结果，更新 T-0004，推送独立分支并产出供人类合并的 PR。

边界检查：所有导航地址必须可达；未知路由显示未找到；刷新保留主题；localStorage 失败不阻塞页面；小屏导航可开关并在跳转后关闭。无密钥、无业务数据和数据库迁移。

交付：分支已推送；GitHub API/GraphQL 返回 Forbidden，PR 尚未创建，正文已保存到 PR_DESCRIPTION.md，等待使用 GitHub 创建链接完成。
