---
id: T-0281
title: 大模型客户端“对话”页按效果图重排：管理器、会话、对话三栏，会话详情浮层
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: apps/web/src/modules/llm_bridge/chat,apps/web/src/services/schema.d.ts,apps/web/tests/llm_chat.test.ts,services/llm-bridge/bridge/app.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/bridge/store.py,services/llm-bridge/tests/test_bridge.py,services/llm-bridge/tests/test_content.py,services/api/app/modules/llm_bridge/api.py,services/api/app/modules/llm_bridge/chat.py,services/api/app/modules/llm_bridge/chat_schemas.py,services/api/app/modules/llm_bridge/schemas.py,services/api/app/modules/llm_bridge/service.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge.py,tests/e2e/llm_chat_browser.cjs,docs/v2/LLM_CHAT_PLAN.md
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0281 大模型客户端“对话”页按效果图重排：管理器、会话、对话三栏，会话详情浮层

## 目标
人类 2026-11-17 在对话里给了两张效果图，要求按图重新优化“大模型客户端”页面的“对话”子页面：

1. 主界面三栏：
   - 左栏“MCP 管理器”卡片列表：名字、会话数、连接状态；可以搜索，有“+”，底部是“管理器设置”。
   - 中栏“会话”：标题和数量、“新建”、“更多”菜单，搜索与平台筛选，按天分组，每个会话一行标题、一行最近一次提问。
   - 右栏对话：标题和“平台 · 用法”标签；回答下面是图标按钮；输入框只留附件、知识库、发送三个图标。
2. 会话详情：
   - 标签：概览 / 执行记录（带数量），以及状态。
   - 依次是：会话卡片、开发环境、上下文（比例、用量、压缩上下文 / 带摘要新建）、账号额度（每个窗口的已用比例与重置时间、管理器近 5 小时用量）、会话纪要（开关、立即整理）、基本信息、更多信息。

## 验收标准
- [x] 1440px 起三栏：左栏是 MCP 管理器卡片（名字、进行中的会话数、已连接 / 未连接 / 已停用），能搜索；“+”只给 owner；“管理器设置”带上选中的管理器
- [x] 901 ~ 1439px 两栏，管理器在会话列表顶上用下拉选；900px 以下列表和对话分屏（和原来一样）
- [x] 会话列表：标题旁写数量，“新建”在右上；多模型同问、同步官方会话、看已归档的会话在“更多”菜单里；平台筛选改成下拉（没登录的标出来）；正在看已归档时有提示
- [x] 每个会话第二行是最近一次提问（MCP 管理器 `/admin/sessions` 新增 `preview`：脱敏、压成一行、最多 120 字，不算压缩 / 交接这类内部回合，清掉内容的会话没有）；旧版本的管理器没有时写平台 · 用法 · 谁建的
- [x] 管理器的会话数来自工作台的会话索引（`LlmBridgeOut.session_count`，只数没归档的；还没同步过为空、不写），不另外连管理器
- [x] 平台用图标区分（Claude 星芒、CodeBuddy 机器人、ChatGPT / Codex 对话六边形、开发会话代码符号；不是商标），列表、头像、会话详情、同问各栏共用
- [x] 对话头部只留“平台 · 用法”标签（能换模型的会话再加模型下拉）；回答下面的复制 / 存进知识库 / 重新生成改成图标按钮（读屏名字不变）；输入框附件、知识库、发送是图标按钮，提示文字放在悬停提示里
- [x] 会话详情按效果图排；默认收起，开关状态记在浏览器里；对话这一栏不够宽时浮在右边，1000px 以上排在旁边
- [x] 后端：管理器 ruff / mypy / pytest；工作台 ruff / mypy / llm_bridge 相关测试 / `export_openapi --check`；前端 lint / typecheck / vitest / build / check:api 全部通过；`llm_chat_browser.cjs` 跟着新结构更新；1680 / 1440 / 1180 / 390 宽度没有横向滚动

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6942505735
- MCP 管理器源码变了（`/admin/sessions` 多一个字段），镜像标签跟着变：已部署的管理器会提示可以重新部署来升级；没升级时页面退回原来的第二行，功能不受影响。
- 没有“点赞 / 点踩”：效果图上有，但管理器和工作台都没有对应的反馈接口，不做假按钮。
- 会话详情里的会话卡片没有效果图上的“…”菜单：这些操作都已经在对话头部的“更多操作”里。
- 交叠：
  - T-0280（claude，集成会话，去掉 ChatGPT 平台）也会改聊天页的平台选择与图标，合并时处理冲突。图标按“平台 + 用法”来定，去掉 ChatGPT 之后 Codex 的对话与开发照样能区分。
  - T-0234（claude）、T-0224（codex）的 paths 宽，但它们的分支都不改这些文件。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 08:38 UTC · claude · 创建任务（owner: unassigned；编号 T-0281 由集成会话在 #235 预留）
- 2026-11-17 08:38 UTC · human · owner unassigned → claude：人类在对话中指派：按两张效果图重新优化大模型客户端页面的“对话”子页面
- 2026-11-17 08:38 UTC · claude · 开始任务
- 2026-11-17 09:09 UTC · claude · 做完：三栏（MCP 管理器卡片 / 会话列表 / 对话）、会话详情按效果图（默认收起，对话栏 1000px 以上排在旁边）；MCP 管理器 /admin/sessions 新增 preview（脱敏、压成一行、最多 120 字、不算内部回合），工作台 LlmChatSessionOut.preview、LlmBridgeOut.session_count（会话索引，没同步过为空）；平台图标组件；测试与 llm_chat_browser.cjs、LLM_CHAT_PLAN 跟着更新。在 main 64bf9ed（含 #244、#245）上：前端 lint、typecheck、vitest 84 个文件 922 个测试、build、check:api 通过；管理器 ruff、mypy、pytest 100 passed / 3 skipped；工作台 ruff、mypy、export_openapi --check、llm_bridge 四个测试文件 45 passed。Vite + Playwright（模拟接口）截图核对 1680 / 1440 / 1180 / 390 与暗色，没有横向滚动。没做点赞 / 点踩（没有反馈接口）。真实浏览器验收以 CI 为准。
- 2026-11-17 09:09 UTC · claude · status in_progress → done：对话页按效果图重排，验收标准全部勾选（ci-llm-bridge 的浏览器验收合并前确认）
- 2026-11-17 09:20 UTC · claude · CI 的 ci-llm-bridge workspace（llm_chat_browser.cjs）在“列表里写明是谁建的”失败：有提问摘要时第二行只写摘要，谁建的只在悬停提示里，列表上看不出别人建的会话。改为别人建的（别的管理员、智能体、官方程序里已有的）第二行写“谁建的 · 提问”，自己的只写提问；单元测试跟着改，llm_chat.test.ts 51 passed，lint、typecheck 通过。
- 2026-11-17 09:28 UTC · claude · CI 的 workspace 又在“清空上下文”失败：改标题那一步之后会话详情一直开着（开关记在浏览器里），1440 宽时它浮在对话上、盖住了头部的“更多操作”。真实使用也会这样，所以浮层改成从对话头部下面开始（量头部高度），头部的搜索、更多操作、详情开关一直点得到；llm_chat_browser.cjs 在打开详情时点一次“更多操作”验证，看完收起。llm_chat.test.ts 51 passed，lint、typecheck 通过；Playwright 模拟接口核对 1440 宽打开详情时“更多操作”能点开。
