---
id: T-0234
title: MCP 管理器一键部署：部署到智能体服务器或容器服务器，两个服务器页面的入口，容器不受它们的管理程序影响
owner: claude
status: in_progress
priority: P1
branch: claude/trusting-euler-480tpv
paths: apps/web/src/modules,apps/web/src/features/agents/ServerDetail.vue,apps/web/src/features/runner,apps/web/tests,apps/web/src/services/schema.d.ts,services/api/app/modules/llm_bridge,services/api/app/runner_bundle,services/api/app/runner_agent,services/api/app/domain/runner_ssh.py,services/api/app/schemas,services/api/migrations,services/api/tests,services/api/openapi.json,services/api/Dockerfile,compose.yaml,tests/e2e,.github/workflows,docs
depends_on: T-0232
created: 2026-11-14
updated: 2026-11-14
---

# T-0234 MCP 管理器一键部署：部署到智能体服务器或容器服务器，两个服务器页面的入口，容器不受它们的管理程序影响

## 目标
人类 2026-11-14 的三条要求（设计方案 `docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md`，先写方案再落实）：

1. 页面上一键部署 MCP 管理器（会话桥容器），不用在服务器上敲命令。
2. 可以部署到别的服务器：从“智能体服务器”或“容器服务器”里选；这两个服务器管理页面各加一个 MCP 管理器容器的入口。
3. MCP 管理器的容器不受这两个页面的管理程序限制——大模型的运行环境要保持稳定。

## 验收标准
- [x] 设计方案 `docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md`；第三方行为的核验记录 `docs/decisions/2026-11-14-mcp-manager-deploy-verification.md`
- [x] 部署目标两类：智能体服务器（保存的密码）与容器服务器（专用密钥；Docker 经 sudo，密码只用一次、不保存、不进日志）
- [x] 网络：和工作台同机加入内部网络；其他服务器只监听本机地址，工作台经专用 SSH 通道访问（只能转发一个端口、不能执行命令的密钥）
- [x] 部署 / 重新部署 / 重启 / 卸载（含“只删除登记”）都是后台任务，页面看每一步；失败回滚不留下带令牌的文件、容器和密钥
- [x] 服务器从工作台移除、改密码、吊销容器服务器的专用密钥后，已部署的管理器照常能用
- [x] 两个服务器页面的“MCP 管理器”区块（前端模块扩展点 `serverPanels`；模块停用后消失）
- [x] 保护规则（方案第 8 节）：标签 `myworkspace.mcp-manager`；gVisor 安装先重新加载 Docker、不无条件重启；容器服务器上报把管理器单独计数；回归测试钉住两个页面的管理程序碰不到管理器
- [x] 两张表与迁移 `0070`（可升可降，与模型一致）
- [x] 权限：部署类操作 owner，查看 owner / admin，member 403；审计不记密码与令牌
- [x] 后端 lint / 类型 / 测试、前端 lint / typecheck / test / build / check:api、浏览器验收，结果如实记录
- [x] 文档：部署指南第 25.5 节、接口、数据字典、模块文档

## 背景 / 约束 / 参考
- 接 T-0229 ~ T-0232。`docs/AGENTS.md` 不改：例外九不变，智能体仍然只能经工作台的工具访问会话桥；工作台不挂 Docker socket，OpenClaw 服务器不开 exec。
- 路径与 T-0224（codex，`handoff`，声明的范围很宽）重叠：本任务改容器服务器的接入包（`install-gvisor.sh`、runner-agent 的上报）和两个服务器页面。
  人类在对话中明确要求改这两个页面与它们的管理程序；codex 的 PR #203 只动迁移、测试和 infra 的 README，没有碰到这些文件。
- codex 暂停中（D-0007）：前端也由 claude 做。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-14 08:11 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-14 08:11 UTC · human · owner unassigned → claude：人类在对话中指派（2026-11-14）：页面上要有一键部署 MCP 管理器；可以部署到智能体服务器或容器服务器，两个服务器管理页面各加一个 MCP 管理器容器的入口；MCP 管理器的容器不受这两个页面的管理程序限制。先写设计方案，再全面落实
- 2026-11-14 08:11 UTC · claude · 开始任务：设计方案 docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 已写；开始落实
- 2026-11-14 08:11 UTC · claude · 路径与 T-0224（codex，handoff）声明的范围重叠（services/api、apps/web）：人类在对话中明确要求改两个服务器页面和它们的管理程序；实际只动 runner 接入包的 install-gvisor.sh、runner-agent 的上报、runner_ssh 的安装请求和两个页面的各一处，PR #203 没有碰这些文件
