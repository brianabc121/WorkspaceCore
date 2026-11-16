---
id: T-0255
title: 大模型客户端：清空会话上下文（换一个新的官方会话，之前的记录保留并画分隔线；聊天页输入 /clear 也会清空）（V2）
owner: claude
status: done
priority: P1
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e,docs
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0255 大模型客户端：清空会话上下文（换一个新的官方会话，之前的记录保留并画分隔线；聊天页输入 /clear 也会清空）（V2）

## 目标
人类在聊天页给 CodeBuddy 的会话发了 `/clear`，模型回答“对话已清空”，之后每回合的 token 照样增加。
原因：管理器每回合用官方程序的非交互模式（`-p --resume <会话号>`）续接同一个原生会话，`/clear` 被当成普通文字发给了模型，
管理器也没有“清空”这个操作。人类选了方案 B：在 MCP 管理器里给会话加“清空上下文”。

## 验收标准
- [x] 管理器：清空上下文 = 同一个会话换一个新的官方会话号（命令行类平台新的 UUID，Codex 新开一个线程），之后的回合从零开始；
      之前的回合、过程、附件、开发会话工作目录里的文件都保留
- [x] 有排队中或进行中的消息时不能清空（409）；已归档、官方程序里已有的会话不支持；还没有任何回合时什么也不做
- [x] 清空前的原生会话号记下来：同步官方程序的会话时不会被当成“官方程序里已有”的会话再登记一次
- [x] 被额度打断、等着自动继续的回合，清空后不再自动续跑
- [x] 会话内容里在清空的位置有一条分隔记录（谁清空的、什么时候）；智能体读会话（`llm_session_get`）也能看到
- [x] 工作台：`POST /api/v1/llm_bridge/bridges/{id}/chat/sessions/{session_id}/clear`，owner / admin、CSRF，
      别人建的会话要 `takeover=true`，否则 409 TAKEOVER_REQUIRED；停用的管理器 409；记审计 `chat_session_context_cleared`
- [x] 聊天页：会话标题旁“清空上下文”（先确认）；输入框里只输入 `/clear` 发送时直接清空，不发给模型；
      以 `/` 开头的其他消息提示“不会执行命令，会当成普通文字发给模型”；对话里在清空的位置显示分隔线
- [x] 管理器 pytest、工作台后端相关测试、前端 lint / typecheck / test / build / check:api 通过；浏览器验收走一遍清空

## 背景 / 约束 / 参考
- `services/llm-bridge/bridge/drivers/cli_stream.py`：每回合 `--session-id`（官方程序里还没有记录）或 `--resume`。
- 不给智能体新增“清空”工具：例外九列出的写工具是人类定的（`docs/AGENTS.md` 第 8 节），要加先问人类。
  智能体要从头开始可以新建一个会话。
- 多模型同问的每一栏暂不加清空（每一栏可以“在这个会话里继续”后在单会话里清空）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 05:15 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-16 05:15 UTC · human · owner unassigned → claude：人类在对话中指派：按方案 B 在 MCP 管理器里加“清空上下文”：会话不变，换一个新的官方会话号，后面的回合从零开始，页面上画分隔线，之前的记录照样能看
- 2026-11-16 05:15 UTC · claude · 开始任务
- 2026-11-16 05:34 UTC · claude · 管理器：POST /admin/sessions/{id}/clear 换新的原生会话、记 context_resets、会话内容插分隔、取消等着的自动续跑；工作台：POST .../chat/sessions/{id}/clear（takeover、审计）；聊天页：清空按钮、输入 /clear 直接清空、其他斜杠命令提示、分隔线。管理器 pytest 75 passed（3 skipped）、ruff/mypy 通过；工作台 llm_bridge 四个测试文件 46 passed、ruff/mypy/openapi 通过；前端 lint/typecheck/880 个测试/build 通过；浏览器验收（tests/e2e/llm_chat_browser.cjs 新增清空一步）在本 PR 的 CI workspace job 里跑。
- 2026-11-16 05:34 UTC · claude · status in_progress → done：代码、测试、文档完成；浏览器验收以本 PR 的 CI 为准，合并前确认
- 2026-11-16 07:53 UTC · claude · 合并后 main 上 ci-llm-bridge 的浏览器验收失败（run 43818013261）：点“清空上下文”后分隔线先画出来，清空请求还没收尾时按 Enter 发的消息被丢掉（submit 里 clearing 为真直接返回）。PR 上那次是碰巧时序对了。修法：清空进行中按发送就等清空做完再发；新增前端测试（旧代码上失败、修复后通过）。前端 lint/typecheck/881 个测试/build 通过。
- 2026-11-16 09:12 UTC · claude · main（f26a9ef）上 Web CI typecheck 和构建镜像失败：#225 给聊天消息加了必填的 references，#226 新增的测试里那条分隔线没带（两个 PR 分别都是绿的，合在一起才冲突）。补上 references: []；typecheck、lint、888 个测试、build、check:api 通过。
