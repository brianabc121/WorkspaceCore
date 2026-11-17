---
id: T-0276
title: 大模型客户端：CodeBuddy 网页登录按智能体服务器初始化的样式显示逐步进度与运行信息
owner: claude
status: done
priority: P1
branch: claude/amazing-faraday-6ey29e
paths: services/llm-bridge/bridge/drivers/web_login.py,services/llm-bridge/bridge/drivers/base.py,services/llm-bridge/bridge/engine.py,services/llm-bridge/tests/test_drivers.py,services/llm-bridge/tests/fakes/fake_cli.py,services/api/app/modules/llm_bridge/schemas.py,services/api/app/modules/llm_bridge/service.py,services/api/app/modules/llm_bridge/api.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge.py,apps/web/src/modules/llm_bridge/LoginProgress.vue,apps/web/src/modules/llm_bridge/BridgeAccounts.vue,apps/web/src/modules/llm_bridge/llm_bridge.css,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts
depends_on: T-0275
created: 2026-11-17
updated: 2026-11-17
---

# T-0276 大模型客户端：CodeBuddy 网页登录按智能体服务器初始化的样式显示逐步进度与运行信息

## 目标
人类 2026-11-17 在对话里要求：现在点“登录 CodeBuddy”后，工作台只能干等，看不到任何运行信息，很难判断到底哪里出了问题。
要按“智能体服务器 → 初始化”的样式（共用组件 `components/JobProgress.vue`）改造登录过程的显示。

## 验收标准
- [x] 网页登录（CodeBuddy 国内站 / 国际站）显示逐步进度：交给 MCP 管理器 → 启动登录服务 → 申请登录网址 → 等你在浏览器里登录 → 确认登录结果。
  - 每一步显示状态、用时和说明；
  - 进行中那一步下面是最新的运行输出；
  - 失败原因挂在失败的那一步下面；
  - 没轮到的步骤显示“未执行”；
  - 运行信息可以展开；
  - 成功后收起成成功条。
- [x] MCP 管理器记下每一步与脱敏后的运行信息：这边做了什么、`codebuddy --serve` 的输出。访问口令、看起来像密钥或令牌的内容都替换成 ***。
  - “查登录进度”接口一并返回；
  - “开始登录”的接口形状和行为不变。
- [x] 拿到网址后在进度下面醒目地给出链接；失败后可以“重新获取登录网址”，也可以关闭；进行中可以“不等了”。
- [x] 只认这一次的进度：开始前记下管理器上一次的 `started_at`，不把上一次的失败或成功当成这一次的。
- [x] 旧版本的管理器（不报告步骤）照样能用：按页面能知道的情况显示，并说明重新部署（升级）后能看到每一步。
- [x] T-0275 的找回逻辑保留：“开始登录”没有完整答复时不重新发起，改查进度，最多 2 分钟。
- [x] 管理器被取消的等待任务（重新开始登录、退出）不再把进度改成“等了 10 分钟没有完成”。
- [x] 检查通过：
  - 管理器：ruff / mypy / 全部测试；
  - 后端：ruff / mypy / openapi / llm_bridge 测试；
  - 前端：lint / typecheck / test / build / check:api；
  - 浏览器截图核对进行中、拿到网址、失败三种状态。

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6937219271
- 没改共用组件 `JobProgress.vue`（智能体服务器初始化、MCP 管理器部署都在用）。
- 设备码登录（OpenAI）保持原来的显示，这次只改网页登录。
- 管理器源码变了，镜像标签随之变化：已经部署的管理器会显示“工作台带的版本比它新，可以重新部署来升级”，升级后才能看到每一步。
- 申请编号时说的“旧等待任务的收尾会把新启动的登录服务关掉”经验证只是理论上的：换回旧代码，测试照样通过，因为旧任务的收尾总在新服务启动前跑完。
  - 真实影响的是：旧任务被取消时会把状态写成“等了 10 分钟没有完成登录”，现在不会了；
  - `stop()` 改为等上一次的等待任务收完尾，属于防御性加固。
- 同模块的 T-0234（claude，进行中）：本任务不碰部署代码。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 04:05 UTC · claude · 创建任务（owner: unassigned；编号 T-0276 由集成会话在 #235 预留）
- 2026-11-17 03:46 UTC · human · owner unassigned → claude：人类在对话中指派：点击登录 CodeBuddy 的过程中看不到任何运行信息，按智能体服务器页面初始化模块的风格改造
- 2026-11-17 03:46 UTC · claude · 开始任务
- 2026-11-17 03:46 UTC · claude · 做完。管理器：WebLogin 记四步（start / request_url / wait_user / finish）的状态、起止时间、说明，运行信息是这边的动作加 codebuddy --serve 的输出（stdout+stderr 逐行脱敏：口令原文、口令 / 令牌 / 密钥后面的值、32 字符以上的一串都换成 ***，最多 120 行，每行 300 字），LoginChallenge 增加 steps / log / started_at / finished_at，engine 的 login / login_state 一并返回；_wait 被取消时不改进度，stop() 等上一次的等待任务收完尾。工作台后端：LlmBridgeLoginStepOut、LlmBridgeLoginOut 加 steps / log / started_at / finished_at，login_out() 转发并限长（12 步、200 行、每行 500 字）。页面：新组件 LoginProgress.vue 用共用的 JobProgress 显示，第一步“交给 MCP 管理器”由页面判断，网址出来后单独一块给出链接，失败可重新获取，旧版本管理器按页面知道的情况显示并提示升级；BridgeAccounts.vue 的网页登录改为一边等“开始登录”一边每 1.5 秒查进度，开始前记下 started_at 只认这一次的，保留 T-0275 的找回；设备码登录不变。测试：管理器 test_codebuddy_web_login 加逐步进度、脱敏、重新开始不改写进度的断言（假的 codebuddy --serve 启动时打印带口令的一行），管理器全部 91 passed / 3 skipped；后端新增 2 个（转发限长、测试会话桥上启动失败的进度经接口给到页面），llm_bridge 三个文件 46 passed；前端 CodeBuddy 网页登录的测试改成新的进度区块，新增管理器报告每一步的成功流程与失败重试，llm_bridge.test.ts 31 个、全量 84 个文件 913 个测试通过；lint、typecheck、build、check:api 通过；Vite + Playwright 截图核对进行中、拿到网址、失败三种状态。
- 2026-11-17 03:46 UTC · claude · status in_progress → done：CodeBuddy 网页登录按初始化的样式显示逐步进度与运行信息，验收标准全部勾选（CI 合并前确认；要看到每一步需要重新部署 MCP 管理器）
