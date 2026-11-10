---
id: T-0212
title: 应用中心验收：浏览器验收、核验记录、文档（方案包 AC-6）
owner: claude
status: in_progress
priority: P1
branch: b6-model-connections
paths: tests/e2e,docs,.github/workflows/ci-infra.yml
depends_on: T-0210, T-0211
created: 2026-11-10
updated: 2026-11-10
---

# T-0212 应用中心验收：浏览器验收、核验记录、文档（方案包 AC-6）

## 目标
应用中心的验收（方案包 AC-6）：真实全栈上的浏览器验收（桌面、手机、暗色、同步失败与重试、导航 409 冲突），接进 CI；
写核验记录（命令、结果、截图说明）、ADR、接口约定与模块文档。

## 验收标准
- [x] tests/e2e/app_center_browser.cjs 覆盖方案包验收表里能在浏览器做的项，接入 ci-infra
- [x] ADR `docs/decisions/2026-11-10-app-center.md`、`docs/api-contract.md`、`docs/modules/README.md`、`docs/v2/MODULES_PLAN.md` 更新
- [x] 核验记录 `docs/verification/T-0212-app-center.md`：真实命令与结果，不把预期当通过
- [ ] PR 的 CI 全部通过

## 背景 / 约束 / 参考
方案包：`docs/v2/app-center/`（设计、接口与一致性契约、前后端计划、验收矩阵、效果图）。方案包里的 AC-0 ~ AC-6 是局部编号：AC-0/1/2 → T-0210，AC-3/4/5 → T-0211，AC-6 → T-0212。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 13:52 UTC · claude · 开始任务
- 2026-11-10 14:58 UTC · claude · 浏览器验收 app_center_browser.cjs（未部署 / 已部署两种模式）本地全栈实跑通过，已接入 ci-infra 的 compose 与 news job；ADR、接口约定、模块文档、核验记录已写；等 PR 的 CI
