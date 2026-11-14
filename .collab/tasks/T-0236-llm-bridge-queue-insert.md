---
id: T-0236
title: MCP 管理器优化：按平台限流、会话内容落库（含图片与文件）、消息队列管理、插入消息；一键部署使用说明
owner: claude
status: in_progress
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/tests,apps/web/src/services/schema.d.ts,tests/e2e,.github/workflows/ci-llm-bridge.yml,docs
depends_on: T-0234
created: 2026-11-14
updated: 2026-11-14
---

# T-0236 MCP 管理器优化：按平台限流、会话内容落库（含图片与文件）、消息队列管理、插入消息；一键部署使用说明

## 目标
人类 2026-11-14 的五条要求（先写优化方案 `docs/v2/LLM_BRIDGE_QUEUE_PLAN.md`，再全面落实）：按平台限流（每个平台同时最多 3 个进行中的会话）、
会话内容（文字、图片、文件）保存在 MCP 管理器的数据库里、消息队列、插入消息、一键部署的使用说明。
人类同日定的三点：按平台计数；做不到插入时降级成“中断后合并重发”；旧会话第一次打开时导入落库。

## 验收标准
- [ ] 优化方案、核验记录 `docs/decisions/2026-11-14-llm-bridge-steer-verification.md`、使用说明 `docs/guides/mcp-manager-deploy.md`
- [ ] 第一步：按平台限流（默认 3）与起跑间隔；队列的查看、修改、置顶（管理器、工作台接口、聊天页、智能体只读工具）
- [ ] 第二步：`messages` / `attachments`、完整过程、已有数据回填、旧会话导入；输入输出附件；聊天页显示
- [ ] 第三步：插入消息（回合中追加 + 降级成中断后合并重发）、聊天页的“排队 / 插入”
- [ ] 每一步：管理器与后端的 lint / 类型 / 测试、前端 lint / typecheck / test / build / check:api、浏览器验收，结果如实记录
- [ ] 文档：部署指南、接口、方案的实施记录

## 背景 / 约束 / 参考
- 接 T-0229 ~ T-0234。分三个 PR 交付（方案第 8 节）；本任务在第三个 PR 合并后才算完成。
- Claude / WorkBuddy 的回合中追加没有账号、不能实测：按官方参数实现，没有确认就走降级。`docs/AGENTS.md` 不改。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-14 13:54 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-14 13:55 UTC · human · owner unassigned → claude：人类在对话中指派（2026-11-14）：MCP 限流（每平台 3 个进行中的会话）、会话内容落库（含图片与文件）、消息队列、插入消息、一键部署使用说明；先写优化方案再全面落实。三点决定：按平台计数、降级成中断后合并重发、旧会话第一次打开时导入
- 2026-11-14 13:55 UTC · claude · 开始任务：优化方案、核验记录、使用说明已写；开始第一步
- 2026-11-14 14:20 UTC · claude · 第一步完成：按平台限流（默认 3）与起跑间隔；队列的查看、修改、置顶（管理器、工作台接口、聊天页、只读工具 llm_bridge_queue）；使用说明。管理器 pytest 29 passed 3 skipped；后端聊天 / 模块 / 契约 53 passed；前端 852 passed；本机浏览器验收 llm_chat_browser.cjs 14 步通过。下一步：第二步（messages / attachments、完整过程、回填、旧会话导入、附件），第三步（插入消息）
- 2026-11-14 16:27 UTC · claude · 第一步（PR #212）已合并。第二步在本地分支做完：管理器 messages / attachments 落库与回填、旧会话导入与重新导入、输入输出附件、下载、过程里的工具调用；工作台接口；聊天页的回形针、缩略图、文件下载。管理器 48 项测试、工作台与前端相关测试、浏览器验收（llm_chat_browser.cjs 16 步）通过。等 PR #213（T-0237）合并后 rebase 到 main 开第二个 PR。下一步：第三步插入消息（Codex turn/steer；Claude / WorkBuddy 走降级：中断后合并重发）。
