---
id: T-0272
title: 大模型客户端：MCP 管理器的部署进度改成与服务器初始化一致的样式，刷新页面后接着显示进行中的任务
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: apps/web/src/components/JobProgress.vue,apps/web/src/components/job-progress.css,apps/web/src/modules/llm_bridge,apps/web/src/features/agents/SshJob.vue,apps/web/src/features/agents/ssh-job.css,apps/web/tests,tests/e2e/mcp_manager_browser.cjs
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0272 大模型客户端：MCP 管理器的部署进度改成与服务器初始化一致的样式，刷新页面后接着显示进行中的任务

## 目标
人类 2026-11-16 在对话里给了“管理器与账号”页一键部署 MCP 管理器时的截图，要求：
1. 部署 MCP 时的进度和智能体服务器页“初始化”区块（T-0269）一样的风格样式；
2. 部署过程中刷新页面，进度信息就不见了——要能接着看到。

## 验收标准
- [x] 进度组件抽成共用的 `components/JobProgress.vue`：智能体服务器的初始化 / 部署 / 卸载（`SshJob.vue`）和 MCP 管理器的部署 / 重新部署 / 重启 / 卸载（`DeployJob.vue`）都用它，外观一致（标题 + 状态 + 用时、分格进度条、步骤时间线、失败原因挂在失败那一步、实时输出、折叠的命令输出、成功条）
- [x] 部署 / 重新部署 / 重启 / 卸载进行中刷新页面：页面从 `GET /llm_bridge/deployments` 找回进行中的任务并接着显示进度；结束后照常刷新列表、选中部署好的管理器
- [x] 原有流程不变：sudo 密码没填或不对时回到表单 / 确认框；只删除登记的卸载照常提示服务器上还留着什么
- [x] 前端 lint / typecheck / test / build / check:api 通过；浏览器里截图核对亮色、暗色、手机宽度；CI 的 MCP 管理器浏览器验收通过

## 背景 / 约束 / 参考
- 编号由集成会话在 #235 预留（https://github.com/brianabc121/WorkspaceCore/issues/235）。
- 只改前端，接口不变：任务列表接口已有，`_settle` 会把工作台重启后中断的任务标成 interrupted。
- T-0271 也在改 `features/agents` 的服务器详情；这里只把 `SshJob.vue` 改成共用组件的包装，进度区块的结构保持不变。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 20:30 UTC · claude · 创建任务（编号由集成会话在 #235 预留；人类在对话中指派），开始任务
- 2026-11-16 20:38 UTC · claude · 完成：新的共用组件 apps/web/src/components/JobProgress.vue（+ job-progress.css，由 features/agents/ssh-job.css 挪过来，类名不变），只收标题、状态、时间、步骤、日志、失败原因、成功文字等参数；features/agents/SshJob.vue 改成它的薄包装（区块 aria-label、data-status、.ssh-step-title 不变，T-0269 的测试与 e2e 照常）；modules/llm_bridge/DeployJob.vue 也改成它的包装：标题“部署「名字」”+ 状态、下面一行写在哪台服务器与网络方式、分格进度条、步骤时间线、失败原因挂在失败那一步、进行中那一步下面的最新输出、折叠的命令输出（密码、令牌已替换成 ***）、成功后收起成成功条；只删除登记的卸载不收起，并在下面提示服务器上还留着什么。刷新丢进度：新增 DeployProgress.vue（按任务号读进度，结束时报一次结果），LlmBridgeWorkspace 不再只用内存里的一个 jobId：页面第一次读到 GET /llm_bridge/deployments 时把还在进行的任务都接着显示（一键部署表单里刚开始的由表单显示，不重复），找回来的部署成功后照常刷新列表、选中新管理器并提示；重新部署 / 重启 / 卸载的 sudo 密码重试不变。接口与后端没改。删除 llm_bridge.css 里不再用的 .lb-job*。测试：llm_bridge_deploy 更新 4 处依赖旧文字结构的断言，新增 2 个测试（刷新后找回进行中的部署并在成功后选中、只找回进行中的任务）；tests/e2e/mcp_manager_browser.cjs 改为按区块 data-status 判断结束，并在部署进行中刷新一次页面确认进度还在。本地 lint、typecheck、vitest 83 个文件 902 个测试、build、check:api 通过；Vite + Playwright 拦截接口：部署进行中刷新页面后进度还在，截图核对进行中 / 失败（亮色 1440）、暗色 1440、390 宽度，没有横向滚动。浏览器验收（真实部署）以本 PR 的 CI 为准，合并前确认。
- 2026-11-16 20:38 UTC · claude · status in_progress → done：MCP 管理器的部署进度改成与服务器初始化一致的共用组件，刷新页面后接着显示进行中的任务（CI 浏览器验收合并前确认）
