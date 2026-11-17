---
id: T-0284
title: 平台连通性检测像官方程序一样换地址（IPv6 / IPv4 交替，0.25 秒），列出试过的每个地址
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge/bridge/connectivity.py,services/llm-bridge/tests/test_connectivity.py,services/api/app/modules/llm_bridge/schemas.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge_chat.py,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,docs/api-contract.md
depends_on: T-0278
created: 2026-11-17
updated: 2026-11-17
---

# T-0284 平台连通性检测像官方程序一样换地址（IPv6 / IPv4 交替，0.25 秒），列出试过的每个地址

## 目标
人类 2026-11-17 反映（编号在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6950642346）：
菲律宾服务器上的 MCP 管理器，“平台连通性”显示三个平台都“连不上”（解析 4～8 秒，之后“建立连接超时，共等了 12 秒”），
但 Codex 已经登录，在对话页也照常能用。检测不准。

## 原因
- 检测按解析结果的顺序一个地址一个地址地连，**不换地址**：第一个地址不应答就一直等到整体超时。
- 官方程序哪个地址先连上用哪个：一个地址 0.25 秒（Node：Claude Code、CodeBuddy，`autoSelectFamily`）或 0.3 秒（Rust：Codex，hyper 的 happy eyeballs）没连上，
  就同时去试下一个，IPv6 / IPv4 交替（RFC 8305）。服务器的 IPv6 不通、IPv4 通时，官方程序照常能用，检测却报“连不上”。
- 解析慢（4 秒、8 秒的整数倍）多半是容器的解析服务器里有一个不应答，要等它超时才换下一个（官方程序也受影响，只是慢）。
  解析和后面几步共用 12 秒，解析用掉 8 秒后，连接只剩 4 秒。
- 另外两处：没开 IPv6 的机器上建不出 IPv6 的套接字时，检测直接报“连不上”，不换地址；一个地址都没连上时，页面还写“建立连接最多只用了 0 ms，网络本身不慢”。
- 用户那台服务器上到底是不是 IPv6 的问题，要等升级后的检测列出每个地址才能确认。

## 验收标准
- [x] 管理器：一个地址 0.25 秒没连上（或已经失败）就同时试下一个，IPv6 / IPv4 交替，先连上的赢，其余的取消；建不出套接字的地址记下来接着试下一个。
- [x] 管理器：每个结果带 `attempts`（地址、4 / 6、毫秒、没连上的原因）；`latency_ms` 是连上的那个地址的用时。域名解析单独限时 15 秒，之后 12 秒从解析完算起。
- [x] 工作台：`LlmBridgeConnectivityTargetOut.attempts` 原样给页面；重新生成 `openapi.json` / `schema.d.ts`。
- [x] 页面：
  - 换过地址或没连上时逐个列出地址；
  - IPv6 没回应、IPv4 连上时，说明官方程序照常能用；
  - 账号已经登录、又不是被平台明确拒绝时，不再断言“登录和对话都会失败”，改成以对话为准；
  - 没有连上过的地址时，不写“建立连接最多只用了 0 ms”。
- [x] 测试：管理器（第一个地址不应答、马上拒绝、全不应答、交替顺序、解析超时）、工作台（字段原样给出）、页面（列地址、IPv6 提示、登录后的说法、不写 0 ms）。
- [x] `docs/api-contract.md`。

## 进度日志
- 2026-11-17 claude：人类在对话中反映检测不准，编号在 #235 预留后开工。
- 2026-11-17 14:17 UTC · claude · status in_progress → done：检测改成和官方程序一样换地址并列出试过的地址；工作台字段、页面说明、测试与文档已完成
