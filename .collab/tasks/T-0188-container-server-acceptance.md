---
id: T-0188
title: 容器服务器（五）：真实容器验收、浏览器验收与升级文档
owner: claude
status: todo
priority: P1
branch:
paths: tests/e2e,.github/workflows/ci-infra.yml,docs
depends_on: T-0187
created: 2026-11-09
updated: 2026-11-09
---

# T-0188 容器服务器（五）：真实容器验收、浏览器验收与升级文档

## 目标
在真实 Docker 上验收 CPU 采样、旧新 agent 兼容、生命周期与归属，并在浏览器里走容器服务器页；更新文档。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 5）

## 验收标准
- [ ] agents 验收的 Runner 场景：两个智能体各建容器，忙 / 闲 CPU 有差异，停止后 CPU / 内存为空、数据卷仍有读数；旧格式报告（不带 CPU）照常对账
- [ ] `runner_browser.cjs` 走容器总览、详情直链、旧 tab 链接、智能体用量、模板、执行记录、设置
- [ ] RUNNER_PLAN、api-contract、data-dictionary、部署文档说明 CPU 口径、数据卷口径与升级顺序（先服务端、再 agent、最后页面）
- [ ] 核验记录写进 docs/verification

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
