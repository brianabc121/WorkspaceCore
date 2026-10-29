---
id: T-0013
title: Sprint 1 集成与验收：DoD 实测、A01/A02/A18 自动化、勾选 TASKS.md
owner: claude
status: todo
priority: P1
branch:
paths: docs/acceptance, docs/TASKS.md, scripts/smoke.sh, .github/workflows/ci-infra.yml, tests/e2e
depends_on: T-0010, T-0011, T-0012
created: 2026-10-29
updated: 2026-10-29
---

# T-0013 Sprint 1 集成与验收：DoD 实测、A01/A02/A18 自动化、勾选 TASKS.md

## 目标
T-0010、T-0011、T-0012 合并后，做前后端联调并按 Sprint 1 的 DoD 实测验收。通过后勾选 `docs/TASKS.md` 的 S1-01 ~ S1-05，请人类批准进入 Sprint 2。

## 验收标准
- [ ] DoD 实测（规范第 13 节）：不登录访问私有接口返回 401；普通用户不能跨工作空间访问；登出后会话失效；迁移可从空库完成
- [ ] 在 Compose 全栈环境下自动化验收场景 A01、A02、A18 与跨工作空间访问（纳入 CI）
- [ ] 浏览器走通：首次安装 → 登录 → 修改设置 → 撤销会话 → 登出
- [ ] `docs/acceptance/sprint-1.md` 逐条列出通过 / 失败 / 未运行及原因（规范第 18 节）
- [ ] 勾选 `docs/TASKS.md` 的 S1-01 ~ S1-05；escalate 给人类，请求批准进入 Sprint 2

## 背景 / 约束 / 参考
- 依赖：T-0010、T-0011、T-0012 都合并之后才开始
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 5.1、6、9 节；Sprint 1 范围与 DoD 见第 13 节

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-29 22:52 UTC · claude · 创建任务（owner: claude）
