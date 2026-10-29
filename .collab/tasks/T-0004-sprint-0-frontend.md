---
id: T-0004
title: Sprint 0 前端：Vue 工程骨架（S0-02）
owner: codex
status: done
priority: P1
branch: codex/t-0004-frontend
paths: apps/web, .github/workflows/ci-web.yml
depends_on:
created: 2026-10-29
updated: 2026-10-29
---

# T-0004 Sprint 0 前端：Vue 工程骨架（S0-02）

## 目标
在 `apps/web` 建立 My Workspace 的前端工程骨架，供后续 Sprint 直接在上面开发页面和 Widget。
只做 Sprint 0 的前端部分；后端、Compose、根目录文件由 claude 的 T-0005 / T-0006 负责（分工见 D-0003）。

## 验收标准
- [x] Vue 3 + TypeScript + Vite，接入 Vue Router（history 模式）、Pinia、TanStack Vue Query、Tailwind CSS
- [x] 基础布局符合 `docs/DEVELOPMENT_SPEC.md` 第 8.1 节：左侧导航 + 顶栏 + 主内容区；导航包含 8.1 节列出的主要页面入口，页面内容为占位；首页有占位组件
- [x] 亮 / 暗主题切换，颜色用 CSS 变量，不硬编码
- [x] `package.json` 提供契约要求的 scripts：`dev`、`build`、`lint`、`typecheck`、`test`；提交 `package-lock.json`
- [x] ESLint、vue-tsc、Vitest 配置完成；至少一个组件测试（例如布局和导航能渲染）
- [x] `vite.config.ts` 把 `/api`、`/healthz`、`/readyz` 代理到 `http://localhost:8000`
- [x] 新建 `.github/workflows/ci-web.yml`（npm ci → lint → typecheck → test → build）
- [x] 前端依赖的选型和版本写进 `docs/decisions/<日期>-frontend-stack.md`，附来源链接；拿不准的写明待定
- [x] 实际运行 `npm ci`、`npm run lint`、`npm run typecheck`、`npm run test`、`npm run build`，把命令和输出写进 PR；没能运行的如实说明原因

## 背景 / 约束 / 参考
- 接口契约：`docs/decisions/2026-10-29-sprint-0-contracts.md`（目录归属、scripts 名称、代理路径、CI 要求）
- 开工指令：`docs/CODEX_START.md`；项目规则：AGENTS.md 第 8 节
- 不要修改 `apps/web` 以外的文件（`ci-web.yml` 除外）；`apps/web/Dockerfile` 由 claude 在 T-0006 编写
- 不要勾选 `docs/TASKS.md`：S0-02 由 Sprint 验收任务 T-0006 统一勾选
- Sprint 0 不连接任何后端或第三方服务，不写假集成数据

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 21:37 UTC · claude · 创建任务（owner: codex）：依据人类提交的 CODEX_START.md（caf32fc），Sprint 0 由 codex 实施
- 2026-10-29 21:49 UTC · claude · 应人类要求重新规划分工（D-0003）：范围调整为前端 S0-02；后端与骨架移到 T-0005，Compose 与验收移到 T-0006；接口契约见 docs/decisions/2026-10-29-sprint-0-contracts.md
- 2026-10-29 22:12 UTC · codex · 开始任务
- 2026-10-29 22:12 UTC · codex · 依据已采纳设计和前后端契约实施；新增 docs/decisions 前端版本 ADR（验收要求，当前无其他占用），计划保存在 apps/web/IMPLEMENTATION_PLAN.md；先验证布局、路由、主题测试，再实现与运行全套前端检查。
- 2026-10-29 22:16 UTC · codex · status in_progress → done：S0-02 前端骨架完成；Node 22.23.4 下 npm ci/lint/typecheck/test(18/18)/build 通过，Vite HTTP 与 history 回退通过；独立审查的小屏导航焦点问题已修复。详见 apps/web/VALIDATION.md；后端和全栈 DoD 由 T-0006 验收，不勾选 docs/TASKS.md。
- 2026-10-29 22:17 UTC · codex · 分支 codex/t-0004-frontend 已推送。gh pr create 实际失败：Post https://api.github.com/graphql: Forbidden（REST 检查同为 Forbidden）；未创建 PR。完整 PR 正文保存在 apps/web/PR_DESCRIPTION.md，可在 GitHub pull/new/codex/t-0004-frontend 创建。Node22 所有前端检查与本地 HTTP smoke 通过；待人类创建/合并 PR，再由 T-0006 联调。
