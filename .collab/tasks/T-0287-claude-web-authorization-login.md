---
id: T-0287
title: Claude 网页授权登录（管理器替人运行官方 claude auth login，页面分步显示、粘贴授权码）；订阅令牌加分步说明
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge/bridge,services/llm-bridge/tests,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge.py,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,tests/e2e/llm_bridge_browser.cjs,docs/api-contract.md,docs/v2/LLM_BRIDGE_PLAN.md,docs/guides/mcp-manager-deploy.md
depends_on: T-0279
created: 2026-11-17
updated: 2026-11-17
---

# T-0287 Claude 网页授权登录（管理器替人运行官方 claude auth login，页面分步显示、粘贴授权码）；订阅令牌加分步说明

## 目标
人类 2026-11-17 在对话中提出（编号在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6954604430，范围后来按人类的决定改成下面这样）：
1. Claude 登录后“身份”是空的（用的是订阅令牌）。
2. Claude 的登录改成和 Codex 一样，能看到全流程卡在哪一步、每一步要人做什么；选“订阅令牌”时用户不知道怎么操作。

人类的决定（对话中选择）：
- “网页授权 + 保留令牌”：新增网页授权登录，授权码经页面转交给官方程序这一点同意（方案 6.3 节已记下）；订阅令牌和 API 密钥保留，订阅令牌加分步说明。
- “撤回，不直接调用”：不由管理器自己拿订阅令牌调 Anthropic 的账号资料接口（最初的做法与 6.3 节“桥只启动未修改的官方程序、桥不读令牌”冲突，没有推送）。账号只用官方程序报告的。

## 核实（锁定的 Claude Code 2.1.323）
- 读安装包源码：`auth status` 只在 `/login` 存下的登录（authMethod `claude.ai`）时报告邮箱；订阅令牌（`CLAUDE_CODE_OAUTH_TOKEN`）时是 `oauth_token`，没有邮箱。所以订阅令牌登录看不到账号是官方程序的行为。
- 实际运行 `claude auth login --claudeai`（临时配置目录，关掉非必要联网，不做真实登录）：没有终端时标准输出依次是 `Opening browser to sign in…`、`If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?...`（权限含 `user:profile`）、`Paste code here if prompted > `。输入没有 `#` 时错误输出 `Invalid code…` 并接着等。
- 读源码：授权码按 `code#state` 解析；换取成功写 `Login successful.` 退出 0；被拒绝写 `Login failed: That code wasn't accepted…` 退出 1；成功后官方程序自己查账号资料、保存凭据，`auth status` 报告 `claude.ai` 和邮箱。
- 用假授权码去请求 Anthropic 的换取接口被本会话的权限策略拦下，没有做；被拒绝时的输出按源码处理。

## 验收标准
- [x] 管理器：Claude 新增登录方式 `web`（排在最前）。
  - 步骤：启动官方程序 → 取得登录网址 → 等人登录授权并交回授权码 → 官方程序完成登录 → 确认账号。
  - 网址 60 秒内没给出算失败；整个登录 15 分钟内没完成就停止。
  - 授权码先查格式（要有 `#`），再写进官方程序的标准输入，不进运行信息。
  - 成功后用 `auth status` 确认，账号由官方程序报告，登录方式显示为“网页授权登录”。
  - 失败（授权码被拒绝、官方程序退出、超时）写明卡在哪一步和原因；没完成之前，原来的登录（例如令牌）照常可用。
- [x] 管理器接口：`POST /admin/accounts/{slot}/login/code`；登录进度带 `needs_code`；没有在等授权码时 409。
- [x] 工作台：
  - `LlmBridgeLoginIn.method` 加 `web`，`LlmBridgeLoginOut.needs_code`；
  - `POST .../accounts/{slot}/login/code`（owner），审计只记“提交了授权码”，不记授权码；
  - 补上两条审计的中文名；重新生成 `openapi.json` / `schema.d.ts`。
- [x] 页面：
  - Claude 默认“网页授权登录（推荐）”；进度卡片分步显示，给出网址、三步说明、授权码输入框；
  - 不完整的授权码页面先拦下；提交后显示“正在完成登录”。
  - “订阅令牌”写出四步怎么拿（`claude setup-token`、授权、粘贴、验证时机），附 Claude 官方说明链接。
- [x] 测试：
  - 管理器：成功、格式不对、被拒绝、不给网址、提前退出、超时、原来的登录在完成前照常可用；接口转交、冲突；
  - 工作台：转交与审计不含授权码；
  - 页面：完整流程与令牌说明；
  - 浏览器验收：在 CI 真实的管理器镜像里取得 claude.com 的登录网址，不登录。
- [x] 文档：方案 6.3 节记下人类的决定，`docs/api-contract.md`、`docs/guides/mcp-manager-deploy.md`。

## 说明
- 只改 MCP 管理器、工作台接口和页面；没有迁移。管理器镜像标签会变，已部署的管理器要重新部署（升级）后才有“网页授权登录”。
- 用订阅令牌登录时仍然看不到是哪个账号：官方程序不报告，按人类决定不另外去查。要看到账号，改用网页授权登录。

## 进度日志
- 2026-11-17 claude：人类在对话中提出；最初直接拿令牌查账号资料的做法与方案 6.3 节冲突，经人类决定撤回，范围改为网页授权登录。
- 2026-11-17 17:18 UTC · claude · status in_progress → done：Claude 网页授权登录、订阅令牌分步说明、接口与页面、测试与文档已完成
