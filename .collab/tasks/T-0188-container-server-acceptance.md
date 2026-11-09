---
id: T-0188
title: 容器服务器（五）：真实容器验收、浏览器验收与升级文档
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
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
- 2026-11-09 10:19 UTC · claude · 开始任务
- 2026-11-09 10:19 UTC · claude · 场景 26 加容器读数检查（gVisor 内存 / CPU 如实、runc 忙闲 CPU、按 CPU 排序带 total、停止后未运行）；runner_browser.cjs 改走容器服务器页；api-contract、data-dictionary（0055）、deploy-guide 8.8（口径与升级顺序）、RUNNER_PLAN 标注；核验记录 docs/verification/T-0188-container-server.md（第 5 节等 CI）。下一步：#180 合并后把本分支并到 claude/cool-wright-99qbbg 推送、开 PR、看 CI agents 结果
- 2026-11-09 12:04 UTC · claude · PR #181 第一次 CI（7c74684）agents 失败：场景 26 第一步“运行服务器上线”等待 240 秒超时，其余 104 项通过。根因：接入包只打包 runner_bundle.AGENT_MODULES 列出的模块，新增的 runner_agent/metrics.py 不在其中，服务器上的 runner-agent 导入 .metrics 失败、从没报到。本地实测走的是源码目录里的 Agent.report()，没经过接入包，所以没发现。修复：AGENT_MODULES 加 metrics；test_runner_agent.py 新增 test_bundle_carries_every_agent_module（runner_agent 目录的模块必须全部进包；解压后用 python -I 只靠包里的代码导入 runner_agent.agent），修复前失败、修复后通过。另外场景 26 中途抛异常时也打印 runner-agent 日志，install() 等不到上线时打印 install.sh 输出（这次日志里看不到 ImportError 就是因为这两处没打）。
