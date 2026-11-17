---
id: T-0278
title: 大模型客户端：平台连通性的延迟改成建立连接的用时，分步显示解析 / 连接 / 握手 / 平台应答，超时写明卡在哪一步
owner: claude
status: done
priority: P2
branch: claude/gracious-euler-ldkldx
paths: services/llm-bridge/bridge/connectivity.py,services/llm-bridge/tests,services/api/app/modules/llm_bridge/schemas.py,services/api/openapi.json,services/api/tests/test_module_llm_bridge_chat.py,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests/llm_bridge.test.ts,docs/api-contract.md
depends_on: T-0240
created: 2026-11-17
updated: 2026-11-17
---

# T-0278 大模型客户端：平台连通性的延迟改成建立连接的用时，分步显示解析 / 连接 / 握手 / 平台应答，超时写明卡在哪一步

## 目标
人类 2026-11-17 在对话中反馈并要求修复：“应用 / 大模型客户端”页面准备登录 ChatGPT 时，“平台连通性”的数字和结论都不对。

- 在菲律宾服务器上 `ping auth.openai.com` 只有 1.7 ms，页面却显示 4648 / 5795 ms。
- api.openai.com、claude.ai、CodeBuddy 两个站一会儿显示“超时（8 秒内没有应答）→ 连不上”，一会儿又“可用”。
- 人类在宿主机上 `curl https://api.openai.com/`、`https://api.anthropic.com`、`https://claude.ai` 都马上有应答。

根因在 `services/llm-bridge/bridge/connectivity.py`（T-0240）：

- “延迟”是一次完整 HTTPS 访问的总耗时：容器里的域名解析、TCP、TLS、平台源站应答、读最多 64 KB 正文，再等最多 1 秒关连接。
- 8 秒超时也按这整个过程算。只要有一步慢，比如容器的 DNS 每次等一个上游超时，就会被判成“连不上”。
- 页面只给一个总数，看不出慢在哪一步。

## 验收标准
- [x] 管理器：分步计时（`dns_ms` / `connect_ms` / `tls_ms` / `response_ms`）。
  - `latency_ms` 改成建立连接的用时，和 ping 可比。
  - 读正文（最多 3 秒）和关连接（最多 1 秒）不计时，也不计入超时。
  - 超时改成只算到收到应答头，上限 12 秒。
  - 连不上时给出卡住的那一步（`stage`）和已完成那几步的用时。
  - 同时给出连上的 IP 和是否经代理。
  - 读应答时连接被关掉（`IncompleteReadError`）不再漏成异常。
- [x] 管理器测试：用本机的 TLS 服务扮演平台，覆盖六种情况。
  - 应答慢、正文吐不完；
  - 拦截页分几段发；
  - 一直不应答（报卡在“等平台应答”）；
  - 连接被拒绝；
  - 经 HTTPS 代理成功；
  - 代理拒绝。
- [x] 工作台接口：`LlmBridgeConnectivityTargetOut` 增加 `timings` / `stage` / `address` / `via_proxy`（都可为空，旧版本管理器照样能用）。重新生成 `openapi.json` 和 `schema.d.ts`，接口测试覆盖透传。
- [x] 页面：
  - 每个地址显示延迟（建立连接）和每一步的用时，慢的那一步标出来，卡住的那一步写“没有完成”。
  - 域名解析慢时说明原因：容器的 DNS 要等超时，官方程序也受影响。这时“用不了”的提示不再建议换服务器。
  - 平台应答慢单独说明。
  - 旧版本管理器的毫秒数注明是总耗时，提示重新部署（升级）。
  - 账号摘要里补一句“域名解析慢”。
- [x] 前端 lint / typecheck / vitest / build / check:api；接口文档更新。

## 背景 / 约束 / 参考
- 管理器源码变了，镜像标签会变；已部署的管理器要在页面上“重新部署”（升级）后才有分步数字。
- 菲律宾服务器上 4–5 秒、8 秒超时的规律很像容器里的 DNS 在等某个上游超时（Docker 内置 DNS 每个上游等约 4 秒）。这还没在那台服务器上核实，所以这次只把每一步测出来、显示清楚。如果核实是 DNS，再另开任务做“在页面上给 MCP 管理器指定 DNS 服务器”（按 2026-11-06 的要求放在 web 端），这次不做。
- 交叠：T-0234（claude，进行中）的 paths 包含 `services/api/app/modules/llm_bridge`、`apps/web/src/modules`，这次不碰部署代码。T-0224（codex，handoff）路径范围很宽，但没有改连通性相关文件。
- 编号由集成会话在 #235 预留（https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6940446702）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-17 06:20 UTC · claude · 创建任务（编号在 #235 预留，owner claude；人类在对话中直接派活：“给我进行修复”）
- 2026-11-17 06:38 UTC · claude · 管理器、接口、页面与测试完成，准备开 PR
- 2026-11-17 06:38 UTC · claude · status in_progress → done：实现与测试完成，随 PR 合并；已部署的管理器要重新部署（升级）后才有分步数字
