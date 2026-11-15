---
id: T-0238
title: 大模型客户端：本机部署 MCP 管理器（随工作台启动，网页上一键添加）；模块默认启用
owner: claude
status: done
priority: P2
branch: claude/trusting-euler-480tpv
paths: compose.yaml,.env.example,Makefile,services/api/app/modules/llm_bridge,services/api/app/core/config.py,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/tests,apps/web/src/services/schema.d.ts,tests/e2e,docs,.github/workflows
depends_on: T-0234
created: 2026-11-15
updated: 2026-11-15
---

# T-0238 大模型客户端：本机部署 MCP 管理器（随工作台启动，网页上一键添加）；模块默认启用

## 目标
人类 2026-11-15 的两条要求：

1. 添加 MCP 管理器的页面多一个“本机部署”，而且默认就是它；选了就自动好，不再填一次服务器的账号密码。
2. “大模型客户端”模块默认开启。

方案：`docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md` 第 9 节——把一个 MCP 管理器并进工作台的 Compose，网页上“本机”只是登记它。

## 验收标准
- [x] `compose.yaml` 多一个随工作台启动的 `llm-bridge-local`；令牌由 `make env` 生成；不发布端口、不挂 Docker socket
- [x] `GET / POST /api/v1/llm_bridge/local`：只有 owner 能添加；地址和令牌不经过页面；一个工作空间一次、不同工作空间不共用；地址令牌不能改
- [x] 添加页面的服务器类型多“本机”并默认选中；已经添加过或没有随带时换到服务器
- [x] 模块 `llm_bridge` 默认启用；相关测试、验收脚本、文档跟上
- [x] 后端、前端的 lint / 类型 / 测试、build；CI 的 `make up` 与浏览器验收（真实启动本机管理器）通过

## 背景 / 约束 / 参考
- 工作台不挂 Docker socket（规则不变），所以不是“工作台去起容器”，而是容器随 Compose 一起起。
- 本会话的沙箱里 Docker 构建装不了系统包，`make up` 的真实构建与启动只在 CI 里验证。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 05:46 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 05:59 UTC · human · owner unassigned → claude：人类在对话中指派（2026-11-15）：添加页面新增默认的“本机部署”，不填服务器账号密码；“大模型客户端”默认开启
- 2026-11-15 05:59 UTC · claude · 开始任务
- 2026-11-15 05:59 UTC · claude · status in_progress → done：实现、测试完成，随本 PR 合并；make up 的真实构建与启动由 CI 验证
