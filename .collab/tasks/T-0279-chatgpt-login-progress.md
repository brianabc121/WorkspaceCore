---
id: T-0279
title: 大模型客户端：ChatGPT（Codex 设备码）登录也按 CodeBuddy 网页登录的样式显示逐步进度与运行信息
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge/bridge/drivers/codex.py,services/llm-bridge/bridge/drivers/web_login.py,services/llm-bridge/bridge/drivers/progress.py,services/llm-bridge/tests,apps/web/src/modules/llm_bridge,apps/web/tests/llm_bridge.test.ts
depends_on: T-0276
created: 2026-11-17
updated: 2026-11-17
---

# T-0279 大模型客户端：ChatGPT（Codex 设备码）登录也按 CodeBuddy 网页登录的样式显示逐步进度与运行信息

## 目标
人类 2026-11-17 在对话中要求：ChatGPT 的登录过程也改成和 CodeBuddy 登录一样的样式，让人能看到整个登录过程的每一步。

原来点“获取验证码”后，页面只有一个设备码框，写着“正在等待你完成登录…”。看不到 MCP 管理器在做什么、卡在哪一步；申请设备码失败时，只在页面顶部显示一句错误。

## 验收标准
- [x] 管理器：Codex 设备码登录记录四步：
  - 启动 Codex 的本机程序；
  - 向 OpenAI 申请设备码；
  - 等你在浏览器里输入验证码并登录；
  - 确认登录结果。

  每一步的状态、起止时间、说明，以及脱敏后的运行信息都随“开始登录”“查登录进度”返回。
  - 验证码不记进运行信息。
  - 申请失败时，查进度能看到失败在哪一步、原因是什么。
  - 浏览器里没有完成授权时，“等你登录”那一步失败。
  - 等待时本机程序退出了，写明原因，不再一直显示“等待中”。
- [x] 记步骤和脱敏的代码抽到 `bridge/drivers/progress.py`，CodeBuddy 网页登录和 Codex 共用；网页登录行为不变。
- [x] 开始时间精确到毫秒，一秒内重试也能分清“这一次”和“上一次”。
- [x] 页面：设备码登录改用和 CodeBuddy 一样的进度卡片（`LoginProgress` / `JobProgress`）。
  - 网址和验证码显示在卡片里；
  - 失败时原因挂在失败的那一步下面，可以“重新获取验证码”；
  - 旧版本的管理器不报告步骤时，按页面知道的情况拼出几步，并提示升级。
  - 去掉原来单独的设备码框和 3 秒一次的轮询。
- [x] 管理器、前端测试；接口形状不变（T-0276 已有 `steps` / `log` / `started_at` / `finished_at`）。

## 背景 / 约束 / 参考
- 管理器源码变了，镜像标签会变。已部署的管理器要在页面上“重新部署”（升级）后才有每一步；没升级的管理器照样能登录，页面会说明。
- Codex 设备码登录的接口（`account/login/start` 的 `chatgptDeviceCode`、`account/login/completed` 通知）在 codex-cli 0.169.0 上只核验过 schema（`docs/decisions/2026-11-13-llm-bridge-verification.md` 第 2 节）。人类 2026-11-17 已在菲律宾服务器上用它登录成功。这次只是在现有调用前后记步骤，没有改调用方式。
- 交叠：T-0234（claude，进行中）不碰部署代码；T-0224（codex，handoff）不改这些文件。
- 编号由集成会话在 #235 预留（https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6940849851）。
- PR #245（T-0278）验证期间它的 head 保持不动：这次的提交接在同一分支的后面，#245 合并后再推送、另开 PR。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 06:42 UTC · claude · 创建任务（编号在 #235 预留，owner claude；人类在对话中直接派活）
- 2026-11-17 07:03 UTC · claude · 管理器、页面与测试完成；等 PR #245 合并后推送并开 PR
- 2026-11-17 07:03 UTC · claude · status in_progress → done：实现与测试完成，随 PR 合并；已部署的管理器要重新部署（升级）后才有每一步
