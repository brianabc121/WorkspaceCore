---
id: T-0286
title: 大模型客户端“管理器与账号”页按效果图改版：管理器卡片带启用开关和“…”，选中打勾
owner: claude
status: in_progress
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: apps/web/src/modules/llm_bridge/LlmBridgeWorkspace.vue,apps/web/src/modules/llm_bridge/ManagerCard.vue,apps/web/src/modules/llm_bridge/llm_bridge.css,apps/web/tests/llm_bridge.test.ts,apps/web/tests/llm_bridge_deploy.test.ts,tests/e2e/llm_bridge_browser.cjs,tests/e2e/mcp_manager_browser.cjs,docs/v2/LLM_BRIDGE_PLAN.md,docs/guides/mcp-manager-deploy.md
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0286 大模型客户端“管理器与账号”页按效果图改版：管理器卡片带启用开关和“…”，选中打勾

## 目标
人类 2026-11-17 在对话里给了一张效果图，要求按图优化“大模型客户端 → 管理器与账号”子页面左边的管理器列表：

1. 列表头：“MCP 管理器 · N”，右边是“添加”；下面是“搜索管理器…”。
2. 每个管理器一张卡片：
   - 第一行：图标、名字、启用开关、“…”，选中的打勾；
   - 第二行：连接状态 | 在哪儿（服务器）；
   - 第三行：“已配置平台：”和平台标签（Cx Codex、Cl Claude、W CodeBuddy）。
   - 选中的卡片浅色底、左边一条色条。
3. 最下面是说明：“每个管理器，每个平台可登录一个账号 / 需要同平台的其他账号时，再添加管理器。”

## 验收标准
- [ ] 列表头写“MCP 管理器 · N”，“添加”（读屏名字“添加 MCP 管理器”，只给 owner）在标题旁；还没有管理器时“添加”在页头
- [ ] 卡片第一行有启用开关（停用前确认；admin 看到灰的开关，只表示状态）和“…”（owner）：手动添加的是修改、删除；工作台部署的是改名、重新部署、重启、卸载；服务器已移除时是改名、卸载（只删除登记）
- [ ] 选中的卡片打勾、浅色底、左边色条；第二行“连接状态 | 在哪儿”，第三行“已配置平台：”和平台标签，放不下时在标签那一栏里换行
- [ ] 右边详情只留“刷新状态”，原来的“管理操作”菜单去掉（操作都在卡片上）
- [ ] 底部说明按效果图两行
- [ ] 前端 lint / typecheck / vitest / build / check:api 通过；`llm_bridge_browser.cjs`、`mcp_manager_browser.cjs` 跟着改选择器；1440 / 1180 / 390 宽度和暗色没有横向滚动

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6953832768
- 只改前端，没有接口和迁移改动。
- 和效果图不一样的一处：第二行写“智能体服务器「名字」/ 容器服务器「名字」”，不只写服务器名——两类服务器在工作台里是分开的，少了类型就分不清。
- T-0224（codex，handoff）的 paths 很宽（`apps/web`、`tests/e2e`），`collab check` 会报交叠；它的分支 `claude/cool-wright-99qbbg` 没有改这些文件。
- 交叠：PR #250（T-0284 + T-0285）改了 `apps/web/tests/llm_bridge.test.ts` 的连通性用例和 `apps/web/src/style.css`（内容区占满宽度）。哪个后合并，后合并的那个按另一个解决冲突；左栏宽度用 `clamp(340px, 38%, 430px)`，内容区变宽后不会无限变宽。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 16:26 UTC · claude · 创建任务（owner: unassigned；编号 T-0286 由集成会话在 #235 预留）
- 2026-11-17 16:26 UTC · human · owner unassigned → claude：人类在对话中指派：按效果图优化大模型客户端“管理器与账号”子页面的管理器列表
- 2026-11-17 16:26 UTC · claude · 开始任务
