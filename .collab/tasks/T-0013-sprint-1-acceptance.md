---
id: T-0013
title: Sprint 1 集成与验收：DoD 实测、A01/A02/A18 自动化、勾选 TASKS.md
owner: human
status: blocked
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance, docs/TASKS.md, scripts/smoke.sh, .github/workflows/ci-infra.yml, tests/e2e
depends_on: T-0010, T-0011, T-0012
created: 2026-10-29
updated: 2026-10-30
---

# T-0013 Sprint 1 集成与验收：DoD 实测、A01/A02/A18 自动化、勾选 TASKS.md

## 目标
T-0010、T-0011、T-0012 合并后，做前后端联调并按 Sprint 1 的 DoD 实测验收。通过后勾选 `docs/TASKS.md` 的 S1-01 ~ S1-05，请人类批准进入 Sprint 2。

## 验收标准
- [x] DoD 实测（规范第 13 节）：不登录访问私有接口返回 401；普通用户不能跨工作空间访问；登出后会话失效；迁移可从空库完成
- [x] 在 Compose 全栈环境下自动化验收场景 A01、A02、A18 与跨工作空间访问（纳入 CI）
- [x] 浏览器走通：首次安装 → 登录 → 修改设置 → 撤销会话 → 登出
- [x] `docs/acceptance/sprint-1.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [x] 勾选 `docs/TASKS.md` 的 S1-01 ~ S1-05；escalate 给人类，请求批准进入 Sprint 2

## 背景 / 约束 / 参考
- 依赖：T-0010、T-0011、T-0012 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 02:41 UTC · claude · 开始任务
- 2026-10-30 02:49 UTC · claude · 验收：全新数据卷的独立 Compose 项目上，tests/e2e/sprint1_acceptance.py 32 项全部通过（A01、A02、A18、跨工作空间 404 / viewer 403、会话撤销、CSRF 与来源校验、审计记录、迁移 head），已加入 ci-infra.yml；tests/e2e/sprint1_browser.cjs（Playwright 1.63.0）走通首次安装 → 登录 → 修改设置 → 撤销会话 → 登出；make lint、make test（后端 71、前端 50）、build-web、check:api、openapi --check 通过；main 上 87cdc16 的 5 个工作流成功。报告 docs/acceptance/sprint-1.md；已勾选 TASKS.md S1-01 ~ S1-05。越出 paths 的文件（均为 claude 负责）：README 当前进度；docs/decisions 前端技术栈 ADR 补录 T-0012 新增依赖的核验结果（D-0003 版本核验 ADR 归 claude，PR #16 审查时已说明），契约 ADR 记录 501 声明暂留至 Sprint 2。
- 2026-10-30 02:49 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-30-0249_T-0013_claude-to-human.md：Sprint 1 验收通过，请批准是否进入 Sprint 2，并确认契约生成文件 schema.d.ts 的提交规则
