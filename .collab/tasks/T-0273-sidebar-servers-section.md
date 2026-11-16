---
id: T-0273
title: 侧栏新增一级分类“服务器”，把智能体服务器与容器服务器挪进去
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: services/api/app/schemas/navigation.py,services/api/openapi.json,services/api/tests/test_navigation_api.py,apps/web/src/app/navigation.ts,apps/web/src/app/sidebar.ts,apps/web/src/modules/types.ts,apps/web/tests/navigation.test.ts,apps/web/tests/agent-server-redesign.test.ts,docs/modules/README.md,docs/deploy-guide.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0273 侧栏新增一级分类“服务器”，把智能体服务器与容器服务器挪进去

## 目标
人类 2026-11-16 在对话里要求：“在左侧新增一个服务器的一级分类，把智能体服务器和容器服务器放到这个分类里面”。
两个页面原来在“连接与工具”里，和 OpenClaw 智能体、开发者、运维、AI 审批混在一起。

## 验收标准
- [x] 侧栏新增内置分组“服务器”（key `servers`），排在“连接与工具”后面、“管理”前面；“智能体服务器”“容器服务器”默认在里面，OpenClaw 智能体仍在“连接与工具”
- [x] 后端侧栏布局把 `servers` 当内置分组：页面、挪分组、分组顺序都能引用它，未知 key 仍然拒绝
- [x] 已保存过布局（设置页会写全部分组的顺序）的工作空间：新分组排在“连接与工具”后面，不会掉到“管理”后面；用户挪过的页面仍在用户的分组
- [x] 设置 → 侧栏与页面里能看到、排序“服务器”分组，页面也能挪进挪出
- [x] 只有能看到这两个页面的角色（owner / admin）才看到这个分组；成员看不到空分组
- [x] 前端 lint / typecheck / test / build / check:api，后端 ruff / mypy / 导航测试通过

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6929159380
- 侧栏布局见 T-0161（`app/schemas/navigation.py`、`src/app/sidebar.ts`）；路由、页面地址不变（`/agent-servers`、`/runner`）。
- 与 T-0271、T-0274 同一个分支 / PR（#241）：云端会话只能推送这一个分支。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 21:10 UTC · claude · 创建任务（owner: unassigned；编号 T-0273 由集成会话在 #235 预留）
- 2026-11-16 21:09 UTC · human · owner unassigned → claude：人类在对话中指派：左侧新增一级分类“服务器”，把智能体服务器和容器服务器放进去
- 2026-11-16 21:09 UTC · claude · 开始任务
- 2026-11-16 21:16 UTC · claude · 做完：后端侧栏布局的内置分组加 servers（白名单与 SectionKey 正则，openapi.json 跟着变；schema.d.ts 不变）；前端 NavSection 加“服务器”，/agent-servers 与 /runner 的默认分组改成“服务器”，分组顺序 工作空间 → 应用 → 连接与工具 → 服务器 → 自定义 → 管理；sidebar.ts 的 sectionOrder 对后加的 servers 做了处理：保存过布局（设置页写全部分组顺序）时排在 tools 后面，不掉到最后。文档：modules/README 的分组列表、deploy-guide 两处侧栏路径。测试：后端 test_servers_is_a_builtin_section；前端导航测试改成新分组并新增“旧布局里新分组排在连接与工具后面、挪过的页面不动”，agent-server-redesign 的侧栏测试改成检查“服务器”分组。本地：后端 ruff / format / mypy / export_openapi --check、test_navigation_api 18 个通过；前端 lint、typecheck、vitest 84 个文件 906 个测试、build 通过；Vite + Playwright 截图核对侧栏。
- 2026-11-16 21:16 UTC · claude · status in_progress → done：侧栏新增一级分类“服务器”，智能体服务器与容器服务器默认在里面，验收标准全部勾选
