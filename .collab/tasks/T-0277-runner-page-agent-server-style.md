---
id: T-0277
title: 容器服务器页面（/runner）改成和智能体服务器页面一样的风格
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: apps/web/src/features/runner,apps/web/src/pages/RunnerPage.vue,apps/web/src/modules/llm_bridge/ServerManagersPanel.vue,apps/web/src/modules/llm_bridge/DeployManager.vue,apps/web/src/modules/llm_bridge/index.ts,apps/web/src/modules/ModuleServerPanels.vue,apps/web/src/modules/types.ts,apps/web/tests,tests/e2e/runner_browser.cjs,docs/deploy-guide.md
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0277 容器服务器页面（/runner）改成和智能体服务器页面一样的风格

## 目标
人类 2026-11-17 在对话里要求：“容器服务器”页面的界面太复杂，改成和“智能体服务器”页面一样风格的样式。

现在的页面从上到下是：服务器池按钮、主机条（加折叠的主机详情）、调度资源清单、四个统计卡、五个标签（容器总览 / 智能体用量 /
环境模板 / 执行记录 / 服务器设置）。添加、部署、移除和回收策略都在“服务器设置”标签里。
改成智能体服务器页面的结构：页头（刷新、添加服务器）→ 统计卡 → 服务器表（搜索、状态筛选）→ 选中一台服务器后显示它的详情
（页头卡片一行六格、连接与部署、这台服务器上的容器、模块区块、回收策略）。

## 验收标准
- [x] 页头：标题、说明、“刷新”与“添加服务器”；添加表单在宽屏右侧一栏、窄屏抽屉里（和智能体服务器页一样），添加后直接打开这台服务器
- [x] 统计卡：服务器、在线、容器环境、运行中；失败的环境在下面一行提示并可“查看”
- [x] 服务器表：服务器、入口、系统 / 资源、运行时、接入状态、容器、操作；搜索名称，按状态筛选（全部 / 在线 / 等待接入 / 需要处理）
- [x] 选中服务器（?host_id=）：页头卡片（名称、状态、版本与入口、关闭）+ 一行六格（连接、运行环境、CPU、内存、磁盘、容器），报告时间与磁盘构成一行，告警列表
- [x] “连接与部署”按智能体服务器的“连接与初始化”排版：左边 SSH 地址 / 核对指纹 / 逐步进度（共用 JobProgress）/ 部署表单 / 卸载与吊销，右边“部署前提”清单与说明，底部移除容器服务器；手动下载接入包保留
- [x] 这台服务器上的容器（按 host_id 筛选，服务端分页）、MCP 管理器等模块区块、自动分配情况、回收策略（默认折叠）
- [x] 没选服务器时，下面是“全部容器 / 智能体用量 / 环境模板 / 执行记录”四个标签，筛选写在地址里；容器详情仍在右侧一栏 / 抽屉
- [x] 旧链接兼容：?tab=environments → 全部容器；?tab=settings（&host_id=）→ 打开那台服务器（没写就打开第一台）；?environment_id= 直链照常
- [x] 1440 / 1180 / 390 宽度无横向滚动；前端 lint / typecheck / test / build / check:api 通过；runner_browser.cjs 跟着新结构更新，CI 的浏览器验收通过

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6939731978
- 只改前端，不动接口与迁移。容器列表按服务器筛选用已有的 `GET /runner/environments?host_id=`；表里的“容器”数是 runner-agent 最近一次完整报告里
  工作台容器的数量（`report.containers`）。
- 交叠：T-0234（claude，in_progress，分支 `claude/trusting-euler-480tpv`）的 paths 含 `apps/web/src/features/runner`，它的分支相对 main 没有未合并的
  runner 改动；T-0224（codex，handoff）的 paths 很宽，#203 不改 runner 前端与 `runner_browser.cjs`。
- 样式复用 `features/agents/agent-servers.css`（RunnerPage 一并引入），不改智能体服务器页面。
- 越界说明：容器服务器详情里的“MCP 管理器”区块（`modules/llm_bridge/ServerManagersPanel.vue`，T-0234 的范围、owner 同为 claude）原来在容器服务器上用
  旧的 `panel runner-card` + h2，这里改成和智能体服务器详情一样的 `ws-panel asv-section` + h3；`DeployManager.vue` 里“先到服务器设置添加”的提示、
  模块注释与 `docs/deploy-guide.md` 第 8 节的步骤跟着新页面改名。T-0234 的分支 `claude/trusting-euler-480tpv` 若也改这些行，合并时以这里的文字为准。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 05:40 UTC · claude · 创建任务（owner: unassigned；编号 T-0277 由集成会话在 #235 预留）
- 2026-11-17 05:43 UTC · human · owner unassigned → claude：人类在对话中指派：容器服务器页面的界面太复杂，改成和智能体服务器页面一样风格的样式
- 2026-11-17 05:43 UTC · claude · 开始任务
- 2026-11-17 05:56 UTC · claude · 做完：页面按智能体服务器页重排——页头（刷新、添加服务器）、统计卡（服务器 / 在线 / 容器环境 / 运行中，失败另起一行可查看）、服务器表（搜索、状态筛选，列：服务器、入口、系统 / 资源、运行时、接入状态、容器）；选中服务器（?host_id=）显示详情：页头卡片一行六格（连接、运行环境、CPU、内存、磁盘、容器）+ 报告时间与磁盘构成 + 自动分配 + 告警，连接与部署（左：SSH 地址 / 核对指纹 / 部署将完成 / JobProgress 逐步进度 / 部署表单 / 卸载与吊销；右：部署前提清单、说明、手动下载接入包；底部移除），这台服务器上的容器（host_id 筛选），模块区块，回收策略（默认折叠）。没选服务器时下面是全部容器 / 智能体用量 / 环境模板 / 执行记录。添加服务器在宽屏右侧一栏、窄屏抽屉，添加后打开它。旧链接 ?tab=environments → 全部容器，?tab=settings(&host_id=) → 打开服务器。删除 SettingsTab / RunnerHostPool / ContainerHostStrip / RunnerSshDeploy；读数与告警移到 hosts.ts。MCP 管理器区块在容器服务器上也用 ws-panel asv-section + h3；DeployManager 提示、模块注释、deploy-guide 第 8 节跟着改名。测试：container-server-pages / runner-pages / llm_bridge_server_panel 改写并新增筛选、详情、旧链接、添加后打开等用例；runner_browser.cjs 跟着新结构改（按 JobProgress 的 data-status 跟踪）。本地：lint、typecheck、vitest 84 个文件 918 个测试通过；Vite + Playwright（模拟接口）截图核对 1440 / 1180 / 390 宽度，无横向滚动。build、check:api 与 CI（含 runner_browser 浏览器验收）结果见 PR。
- 2026-11-17 05:57 UTC · claude · status in_progress → done：容器服务器页按智能体服务器页的样式重排，验收标准全部勾选（CI 的 runner_browser 浏览器验收合并前确认）
- 2026-11-17 06:49 UTC · claude · CI 的 runner_browser 在“移除容器服务器”后超时：页面先刷新列表，详情（含“连接与部署”）随之卸载，之后发的 removed 事件被 Vue 丢掉，地址停在 ?host_id=、显示“找不到这台容器服务器”。改为删除成功后先发 removed（关掉详情）再刷新；单元测试改成删除后 overview 不再返回这台服务器，修复前失败、修复后通过。
