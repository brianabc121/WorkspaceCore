---
id: T-0228
title: 大模型会话桥（二）：工作台模块 llm_bridge（登记会话桥、按实例的权限、llm_* 工具转发、设置接口与页面）
owner: claude
status: in_progress
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/api,apps/web,docs
depends_on:
created: 2026-11-13
updated: 2026-11-13
---

# T-0228 大模型会话桥（二）：工作台模块 llm_bridge（登记会话桥、按实例的权限、llm_* 工具转发、设置接口与页面）

## 目标
落实 `docs/v2/LLM_BRIDGE_PLAN.md` 第 15 节的第 6、7 项：工作台里的插拔式模块 `llm_bridge`。
owner 在页面上登记会话桥、登录各平台、配置开发环境、给实例授权；实例经工作台的 `/mcp` 用 `llm_bridge_*` 工具，
工作台认出实例、检查权限后转给会话桥。

## 验收标准
- [x] 后端模块：两张表与迁移 0066、会话桥客户端、登记 / 状态 / 登录 / 环境 / 设置 / 会话 / 实例权限接口、10 个工具（6 读 4 写）
- [x] 模块系统的两个扩展点（`tools_for_instance`、`direct_tools`），文档已更新
- [x] 整链路测试：实例 → 工作台 /mcp → 模块 → 真实的会话桥应用（进程内）
- [x] 前端页面与测试（lint / typecheck / test / build / check:api）
- [x] 文档：接口、数据字典、部署指南第 25 节、Makefile 命令、同机部署的网络叠加文件
- [ ] 后端全量测试通过
- [ ] **人类明确批准“例外九”**（发送类工具不审批）后，才把它写进 `docs/AGENTS.md`、才合并本任务的 PR

## 背景 / 约束 / 参考
- 写工具做成 direct 是放宽审批：代理不能自己把例外写进 `docs/AGENTS.md`（本会话尝试写入时被权限策略拦下，理由成立）。
- codex 的 T-0224 占着 services/api、apps/web；本任务新增模块目录，另外改了 `app/modules/base.py`、`registry.py`、`service.py`、
  `app/domain/mcp_tools.py` 各几行（扩展点）和 `openapi.json`、前端的生成类型，合并时可能与 T-0224 冲突，需要重新生成。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 10:37 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 10:37 UTC · claude · 开始任务
- 2026-11-13 10:49 UTC · claude · 人类 2026-11-13 在对话中要求全面落实方案（任务建好时 owner 已是 claude）。后端模块、迁移 0066、扩展点、整链路测试（5 通过）、文档已写好并本地提交；前端页面由子代理在做；后端全量测试在跑。例外九没有写进 AGENTS.md，等人类批准。
- 2026-11-13 11:10 UTC · claude · 前端页面完成（子代理实现，我复核）：lint、typecheck、build、check:api 通过，vitest 80 个文件 795 项通过（新增 13 项）；没有做浏览器验收。CI 上发现并修了两个后端契约问题：接口模型名与已有的 SessionOut / LoginIn 等冲突（加 LlmBridge 前缀）、路由函数名作 operationId 与登录模块重复（加 llm_bridge_ 前缀）。后端全量测试仍在跑。合并仍等人类批准例外九。
