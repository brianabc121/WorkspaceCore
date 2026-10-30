---
id: T-0017
title: Sprint 2 集成与验收：DoD 实测、A03/A04 自动化、浏览器 E2E 纳入 CI、勾选 TASKS.md
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,tests/e2e,.github/workflows/ci-infra.yml,scripts/smoke.sh
depends_on: T-0015, T-0016
created: 2026-10-30
updated: 2026-10-30
---

# T-0017 Sprint 2 集成与验收：DoD 实测、A03/A04 自动化、浏览器 E2E 纳入 CI、勾选 TASKS.md

## 目标
T-0015、T-0016 合并后做前后端联调，并按 Sprint 2 的 DoD 实测验收。通过后勾选 `docs/TASKS.md` 的 S2-01 ~ S2-05，请人类批准进入 Sprint 3。

## 验收标准
- [x] DoD 实测（规范第 13 节）：拖拽后刷新布局不丢；未知 widget type 安全报错；部分 Widget 失败不影响整页
- [x] 在 Compose 全栈环境下自动化 A03（viewer 修改 Widget 返回 403）、A04（保存并刷新首页布局一致，版本号更新）并纳入 CI；A05 在尚无 Connector 的情况下以模块 unavailable / stale 的降级展示验证
- [x] 浏览器 E2E（S2-05）纳入 CI：首页加载 → 拖拽保存 → 刷新后布局一致；同时把 Sprint 1 的浏览器流程纳入（`tests/e2e` 已有锁定版本的 Playwright）
- [x] `docs/acceptance/sprint-2.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [x] 勾选 `docs/TASKS.md` 的 S2-01 ~ S2-05；escalate 给人类，请求批准进入 Sprint 3

## 背景 / 约束 / 参考
- 依赖：T-0015、T-0016 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 13 节（Sprint 2 DoD）、第 15 节（A03、A04、A05）、第 18 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 03:04 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 06:12 UTC · claude · 开始任务
- 2026-10-30 06:12 UTC · claude · 人类在对话中说明 Codex 正忙别的任务、让我先做其他事：偏离任务“T-0015、T-0016 都合并后才开始”的约定，先做不依赖前端的部分——Compose 全栈接口级 A03/A04/A05 与 DoD（未知组件类型 422、单个组件数据损坏不影响首页）自动化并纳入 ci-infra。拖拽布局、浏览器 E2E（含 Sprint 1 浏览器流程）在 T-0016 合并后再做：T-0016 会改顶栏/侧栏，现在把浏览器流程放进 CI 可能卡住 Codex 的 PR。
- 2026-10-30 06:19 UTC · claude · 第一部分完成（接口级，本 PR）：tests/e2e/sprint2_acceptance.py 共 38 项（A03/A04/A05、版本冲突、跨工作空间、DoD 未知类型 422、损坏组件不影响首页、审计不含内容），纳入 ci-infra；共用 HTTP 工具抽到 tests/e2e/e2e_support.py，sprint1_acceptance.py 改为导入（行为不变）；docs/acceptance/sprint-2.md 为进行中版本。下一步（T-0016 合并后）：浏览器 E2E（拖拽保存刷新、未知类型错误卡片、模块降级展示）连同 Sprint 1 浏览器流程纳入 CI，补全验收报告，勾选 S2-01~S2-05，escalate 请求进入 Sprint 3。
- 2026-10-30 08:07 UTC · claude · 第二部分（本 PR）：T-0016（PR #23）审查合并后，新增 tests/e2e/sprint2_browser.cjs（真实后端 9 步：拖拽与调整大小保存刷新、两会话 409 锁定、组件增删、本地搜索与暗色、损坏组件错误卡片、390px 单列、viewer 只读）与 tests/e2e/seed.py（Python 与 Node 共用的测试数据写入）；ci-infra 增加 Playwright 安装、Sprint 2 浏览器验收，以及清空数据卷后的 Sprint 1 浏览器验收。越出 paths 的改动及原因：(1) services/api/app/domain/dashboards.py 与对应测试——浏览器验收发现布局无法解析的组件被兜底放在第 999 行，桌面页面被撑到 72551px，违背 DoD“部分 Widget 失败不影响整页”，改为紧接在其余组件下方（T-0015 已 done，同属 claude，无人占用该路径）；(2) docs/decisions 的契约 ADR 第 5 节与前端技术栈 ADR（补录 GridStack 14.0.0 核验，D-0003 规定版本核验 ADR 由 claude 负责）。验收报告 docs/acceptance/sprint-2.md 定稿，勾选 TASKS.md S2-01~S2-05。
- 2026-10-30 08:07 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-30-0807_T-0017_claude-to-human.md：Sprint 2 验收通过，请批准是否进入 Sprint 3（Vikunja）
- 2026-10-30 13:52 UTC · human · owner human → claude：人类在对话中批准进入 Sprint 3（回复“批准进入 Sprint 3”）；未指定 Vikunja 实例或版本，由 claude 在 S3-01 选定稳定 release 并核验；指派 claude 关闭验收任务
- 2026-10-30 13:52 UTC · claude · 解除阻塞，继续
- 2026-10-30 13:52 UTC · claude · status in_progress → done：Sprint 2 验收通过，人类已批准进入 Sprint 3
