---
id: T-0181
title: 智能体服务器（四）：独立的“智能体服务器”页面、连接表单、初始化与已部署清单，旧链接兼容
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/AgentServersPage.vue,apps/web/src/app,apps/web/src/features/agents,apps/web/src/pages/AgentsPage.vue,apps/web/tests
depends_on: T-0180
created: 2026-11-09
updated: 2026-11-09
---

# T-0181 智能体服务器（四）：独立的“智能体服务器”页面、连接表单、初始化与已部署清单，旧链接兼容

## 目标
新增独立页面“智能体服务器”（`/agent-servers`）：四项信息添加服务器、身份确认、测试连接、初始化、补录密码与旧公钥退役、已部署智能体清单；旧链接兼容。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 4）

## 验收标准
- [ ] 侧栏有“智能体服务器”与“OpenClaw 智能体”两个独立入口（owner）；`/agents?tab=servers&server=` 用 replace 转到新页面；自定义导航不丢配置
- [ ] 添加表单只要 IP、端口、账号、密码（名称可选）；密码只在组件内存，不进存储、URL、查询缓存
- [ ] 统计来自服务端 summary；初始化状态、连接状态、指标分别显示，没有上报显示“暂无上报”
- [ ] 详情列出该服务器上的全部智能体（来源、限额、最新任务、跳转），“前往部署智能体”带预选服务器
- [ ] 新测试与既有 agents / navigation 测试通过；亮 / 暗、桌面 / 手机截图

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
