---
id: T-0275
title: 大模型客户端：CodeBuddy“获取登录网址”在 15 秒被浏览器取消，改为等得更久并在超时后自动找回网址
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: apps/web/src/modules/llm_bridge/api.ts,apps/web/src/modules/llm_bridge/BridgeAccounts.vue,apps/web/src/stores/auth.ts,apps/web/tests/llm_bridge.test.ts,services/api/app/modules/llm_bridge/client.py,services/api/app/modules/llm_bridge/service.py,services/api/app/modules/llm_bridge/api.py,services/api/tests/test_module_llm_bridge.py
depends_on:
created: 2026-11-17
updated: 2026-11-17
---

# T-0275 大模型客户端：CodeBuddy“获取登录网址”在 15 秒被浏览器取消，改为等得更久并在超时后自动找回网址

## 目标
人类 2026-11-17 反馈：大模型客户端里 CodeBuddy 选“网页登录（国际站）”点“获取登录网址”，页面报“连接中断或超时，操作结果未知”。
人类用浏览器开发者工具确认：`POST …/accounts/workbuddy/login` 在 15.03 秒被浏览器取消。

根因：
- 页面的写请求只等 15 秒；
- MCP 管理器开始网页登录要先启动 `codebuddy --serve`（最多等 40 秒），再向平台申请登录网址（最多 60 秒）；
- 工作台等管理器的读超时是 65 秒；
- 页面放弃后不会再去取已经申请到的网址，重试又会重启登录服务，所以一直过不去。

## 验收标准
- [x] “开始登录”这一个请求页面等 95 秒（站点经 Cloudflare，单个请求上限 100 秒），工作台等管理器 90 秒；其他请求和管理操作的超时不变
- [x] 等待期间说明在做什么：“正在启动「CodeBuddy」的登录服务并申请登录网址，可能要一分钟左右”
- [x] 开始网页登录的请求没有完整答复（连接中断 / 页面超时、Cloudflare 524 等网关超时、工作台等管理器超时）时，不重新发起，改为每 3 秒查登录进度，最多 2 分钟；查到网址就照常显示链接，接着等登录完成
- [x] 2 分钟还没网址：说明原因与下一步（稍后重试、看管理器日志或改用 API 密钥），不当成已登录
- [x] 不改 MCP 管理器镜像、不改接口形状（不需要重新部署管理器）
- [x] 前端 lint / typecheck / test / build / check:api，后端 ruff / mypy / openapi / llm_bridge 测试通过

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6936893113
- 同一模块的 T-0234（claude，进行中，MCP 管理器一键部署）：本任务不碰部署代码（deploy*.py、deploy_files、部署相关前端组件）。
- 管理器侧的 40 + 60 秒最坏情况超过 90 秒时，工作台会先超时；页面这时也转去查进度，管理器随后申请到网址仍能显示。
- `apps/web/src/stores/auth.ts` 多了一个 95 秒的请求客户端 `longApi`，只给“开始一件在另一台服务器上很慢的事”用。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 03:15 UTC · claude · 创建任务（owner: unassigned；编号 T-0275 由集成会话在 #235 预留）
- 2026-11-17 03:23 UTC · human · owner unassigned → claude：人类在对话中反馈并确认：CodeBuddy 获取登录网址在 15 秒被浏览器取消（开发者工具 15.03 s canceled），要求排查并修复
- 2026-11-17 03:23 UTC · claude · 开始任务
- 2026-11-17 03:23 UTC · claude · 做完：后端 BridgeClient.admin / service.admin / admin_write 加可选 read_timeout（dataclasses.replace 只换这一次的读超时，熔断计数照常共用），开始登录用 LOGIN_READ_TIMEOUT=90 秒，其他仍 65 秒；前端 auth store 加 95 秒的 longApi，api.login 用它；BridgeAccounts.vue：网页登录请求进行中显示说明，请求没有完整答复（WRITE_UNKNOWN、EXTERNAL_UNAVAILABLE、502 / 504 / 52x 网页）时不重新发起，转为每 3 秒查登录进度（最多 2 分钟），查到网址就显示链接，超时说明原因。测试：后端 test_starting_a_login_waits_longer_than_other_admin_calls（经测试会话桥核对开始登录 90 秒、查进度与状态 65 秒）；前端新增 3 个（连接中断、Cloudflare 524 后找回网址且只发起一次，等待提示与找回超时的说明）。本地：后端 ruff / format / mypy（app migrations tests scripts）/ export_openapi --check、test_module_llm_bridge + deploy + chat 共 44 个通过；前端 lint、typecheck、vitest 84 个文件 911 个测试、build、check:api 通过。e2e llm_bridge_browser.cjs 只检查“获取登录网址”按钮在、不点它，不受影响。
- 2026-11-17 03:23 UTC · claude · status in_progress → done：CodeBuddy 获取登录网址等得更久并在超时后自动找回网址，验收标准全部勾选（CI 合并前确认）
