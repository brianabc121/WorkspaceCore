---
id: T-0095
title: IM 消息页改为独立页面（聊天应用式布局）：电脑端三栏、移动端单栏，按效果图落实
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/im,apps/web/src/pages/ImPage.vue,apps/web/src/App.vue,apps/web/src/app/router.ts,apps/web/src/style.css,apps/web/tests/im.test.ts,apps/web/tests/create-menu.test.ts,tests/e2e/sprint6_browser.cjs,docs/decisions,docs/architecture.md,docs/deploy-guide.md
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0095 IM 消息页改为独立页面（聊天应用式布局）：电脑端三栏、移动端单栏，按效果图落实

## 目标
原来的 IM 页面套在工作台的侧栏与顶栏里，由按钮和表单组成，信息密度低，手机上也不像聊天应用。
人类要求参照常见聊天应用（Telegram 一类）的样式重做为单独的页面，分电脑端与移动端；先出效果图
（https://claude.ai/artifact/FcmE8KD2Jhibqzt3rHT9Q4 ），确认后全面落实。

## 验收标准
- [x] `/im` 是独立页面：不显示工作台侧栏、顶栏、底部导航与 AI 抽屉；左上角菜单可返回工作台、打开通讯录、新建群聊、切换夜间模式
- [x] 电脑端三栏（会话列表 / 聊天 / 会话信息，可收起），中等宽度信息栏覆盖在聊天上，手机一次一栏、系统返回键逐层返回
- [x] 会话列表：搜索、分类（会话类型 + 通讯录分组，带有未读的会话数）、头像与在线状态、置顶 / 免打扰 / 停用标记、未读数、右下角新建
- [x] 聊天：顶栏（人数与在线实例数 / 实例状态）、置顶消息条、日期分隔、气泡（发言人、@ 了我、时间与发送状态）、图片、审批卡片、消息操作（置顶、发送失败时重新发送）、@ 成员、Enter 发送
- [x] 会话信息：免打扰、置顶、导出；群公告与群名称编辑、成员拉入 / 移出、防刷屏、解散群
- [x] 原有功能与安全约束不变（幂等发送、结果未知不重复发送、外部图片不加载、owner / admin 专用、通知会话只读）
- [x] 亮 / 暗两套颜色，对比度不低于 4.5:1；390px 宽度无横向滚动
- [x] `apps/web` 的 lint / typecheck / test / build / check:api 通过；本机浏览器截图检查；Sprint 6 浏览器验收脚本按新页面更新

## 背景 / 约束 / 参考
- 设计说明：`docs/decisions/2026-11-03-im-standalone-chat-ui.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 16:17 UTC · claude · 创建任务（owner: claude）
- 2026-11-03 16:17 UTC · claude · 开始任务
- 2026-11-03 16:17 UTC · claude · 人类在对话中指派：按 IM 效果图（https://claude.ai/artifact/FcmE8KD2Jhibqzt3rHT9Q4）全面落实为独立页面：电脑端三栏（会话列表 / 聊天 / 群信息）、移动端会话列表 / 聊天 / 群信息与侧滑菜单
- 2026-11-03 16:43 UTC · claude · 实现完成：独立消息页（路由 meta standalone，App.vue 不渲染工作台外壳），ImWorkspace / ConversationList / ContactsPanel / ChatWindow / MessageItem / ChatInfo / GroupDetails / ImMenu / ChatAvatar / usePopover / format.ts / im.css；旧 IM 样式里只删掉没人用的规则（im-tabs、im-group-form 等实例页仍在用）。越界说明：create-menu 测试（从消息页回工作台再用创建菜单）、docs/architecture.md 与 docs/deploy-guide.md 各改一句页面描述，已加进 paths。检查：apps/web lint / typecheck / test（378+）/ build / check:api 通过；Playwright + 模拟接口截图（1440 亮暗、1024、390 列表 / 聊天 / 群信息 / 菜单 / 通讯录）无横向滚动、无页面错误。sprint6_browser.cjs 按新页面更新，真实 OpenIM 的验收由 CI ci-infra 跑。
- 2026-11-03 16:46 UTC · claude · status in_progress → done：独立消息页按效果图落实：电脑端三栏、移动端单栏、侧滑菜单；检查与浏览器截图通过
- 2026-11-03 16:58 UTC · claude · 处理 PR #138 的 Codex 审查（P1 一条、P2 两条）：1) 草稿、重发的失败消息、图片各用各的幂等键（原来重发和发图片都会清掉草稿还在“结果未知”的键，发图片这处是改版前就有的），新增测试（改回旧做法时失败）；2) 手机上 push 时在历史 state 里记下上一层（imParent），返回只在上一条历史就是上一层时后退，否则在消息页里回到上一层；3) 跳到置顶消息时先往前加载（最多 10 页），找不到就显示全文，置顶条加 title。apps/web lint / typecheck / test（382）/ build 通过。
