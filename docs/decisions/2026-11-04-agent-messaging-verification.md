# 智能体主动发消息：锁定版本上的核验记录（T-0116）

- 日期：2026-11-04
- 状态：已核验（只读源码与镜像里的文档；实测项见第 5 节，由实施任务完成）
- 关联：`docs/v2/AGENT_MESSAGING_PLAN.md`、`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`（OC-0）

## 1. 版本

| 组件 | 版本 | 核对的材料 |
|---|---|---|
| OpenIM Server | v3.8.3-patch.18（上游提交 `120cee6d5783959a02ea1acfa414589fe5aae90f`，本仓库的补丁镜像以它为底） | 该提交的源码 |
| `@openim/openclaw-channel` | 0.3.3（tarball sha256 `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`） | tarball 里的 `dist/*.js` |
| OpenClaw | 2026.9.36（`openclaw/openclaw@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`） | 镜像里 `/app/docs` 的文档，`--network none` 运行 |

## 2. OpenIM

1. **消息元素的字段**（`pkg/apistruct/msg.go`），`/msg/send_msg` 按 `contentType` 用它们校验 `content`（`internal/api/msg.go` 第 166 ~ 169 行）：
   - 图片 102 `PictureElem`：`sourcePicture`、`bigPicture`、`snapshotPicture` 都必填，每个都要有 `type`、`width`、`height`、`url`（工作台现在发图片就是这样，`app/domain/im_service.py`）。
   - 视频 104 `VideoElem`：必填 `videoUrl`、`videoType`、`videoSize`、`duration`、`snapshotUrl`、`snapshotWidth`、`snapshotHeight`；`videoPath`、`videoUUID`、`snapshotPath`、`snapshotUUID`、`snapshotSize` 可选。**发视频必须带封面图和时长**。
   - 文件 105 `FileElem`：必填 `sourceUrl`、`fileName`、`fileSize`；`filePath`、`uuid` 可选。
   - 语音 103 `SoundElem`：必填 `sourceUrl`、`duration`（≥ 1）。方案不做语音。
2. **经管理接口发出的消息同样经过 before 回调**：
   - `/msg/send_msg` 只允许 imAdmin 调用（`internal/api/msg.go` 的 `IsAppManagerUid`），转给消息 RPC 的 `SendMsg`。
   - 群聊：`sendMsgGroupChat` 先调 `webhookBeforeSendGroupMsg`（`internal/rpc/msg/send.go` 第 57 行）。
   - 单聊：`messageVerification` 调 `webhookBeforeSendSingleMsg`（`internal/rpc/msg/verify.go` 第 62 行）；只有发送者是 imAdmin 本身、或者是通知类消息时才跳过。
   - 所以工作台以实例的身份（`sendID = oc_…`）经管理接口代发时，回调照样判断，规则与插件经 WebSocket 发的完全一样。
3. **回调不按消息类型过滤**：`FilterBeforeMsg` 只看配置的 `allowedTypes` / `deniedTypes`（`pkg/common/webhook/content_type.go`）；
   `infra/openim/compose.yaml` 没有设置这两项，图片、文件、视频都会进回调。
4. **回调请求带 `ex`、`contentType`、`content`**（`pkg/callbackstruct/common.go` 的 `CommonCallbackReq`）。
   `ex` 原样来自 `MsgData.ex`，发送方可以自己写，所以不能单凭它认人；方案用工作台签名的值（第 6.4 节）。

## 3. openclaw-channel 0.3.3

1. **主动发只有文字**：渠道的 `outbound` 只有 `sendText`（`channel.js` 第 23 ~ 46 行），出错时返回 `{ ok: false, error }`。
   没有 `sendMedia`，OpenClaw 的 `message` 工具经这个渠道只能发文字。
2. **自动回复只发文字**：`deliver` 回调里 `if (!payload.text) return`（`inbound.js` 第 386 ~ 387 行），回复里的图片等附件被丢掉。
3. **自动回复被拒收时智能体不知道**：发送失败只记一行日志 `deliver failed: …`（`inbound.js` 第 393 行），不重试，也不告诉会话（与 OC-0 #5 一致）。
4. **插件的媒体工具**（`openim_send_image/file/video`）按路径 `readFile` 本机文件（`media.js` 第 3、52 行），而且 OpenClaw 2026.9.36 不让插件注册智能体工具（OC-0 F3）。接入包已禁用，方案不启用。
5. **收消息**：图片下载后作为图片输入交给模型（单张最多 20 MB，超时 15 秒）；视频只取封面；文件只给模型一行
   `[File] name=… type=… url=… size=…` 文字（`inbound.js` 第 6 ~ 7、70 ~ 180 行）。
6. **谁的消息会被处理**：有白名单时只处理白名单里的人；群里还要 @ 到它（第 298 ~ 303 行）。
   接入包的白名单是**签发接入包时**的 owner / admin 和全部实例（`app/domain/agents.py` 的 `_whitelist`），之后新增的管理员要重新签发接入包，智能体才收得到他的私聊。

## 4. OpenClaw 2026.9.36（镜像里的文档）

1. **插件可以往会话里放“系统事件”**：`api.runtime.system.enqueueSystemEvent(text, options)`，带上解析好的 `agentId` 和 `sessionKey`
   （`docs/plugins/sdk-runtime/state-and-system.md`）。同一节还有 `requestHeartbeat(...)`、`runHeartbeatOnce(...)`。
2. **`messaging` 档位包含的工具**（`docs/gateway/config-tools/tool-policy.md`）：`group:messaging`（即 `message`）、`sessions*`、
   `conversations_list`、`conversations_send`、`conversations_turn`、`sessions_send`、`sessions_spawn`、`sessions_yield`、`subagents`、
   `session_status`、`gateway`（只有 update）、`ask_user`。接入包现在只禁用了其中的 `sessions_spawn`、`sessions_send`。
3. **`conversations_send` / `conversations_turn`** 也是往外发消息的路径（发给一个外部会话；后者还等对方回复），只给 owner 用
   （`docs/concepts/session-tool.md`）。它们同样走渠道的 `sendText`，回调照样拦。
4. **命令行 `openclaw agent`** 可以给指定会话发一条消息、让智能体跑一轮：`--session-key`、`--session-id`、`--agent`、
   `--message` / `--message-file`、`--deliver`（`docs/tools/agent-send.md`）。
5. 接入包已用 `openclaw.mjs automations add --session isolated --no-deliver` 建每晚复盘（T-0100，`docs/decisions/2026-11-03-knowledge-k0-verification.md`）。
   智能体自己的 `automations` 工具在 `group:automation` 里，接入包禁用。

## 5. 还要实测的（实施任务里做，结果补进本文件）

| # | 项目 | 实测方法 |
|---|---|---|
| V1 | 以实例身份经 `/msg/send_msg` 发 104、105：成功后插件给模型的文字；被回调拒收时返回的 `errCode` | 真实 OpenIM + OpenClaw，`tests/e2e/agents_acceptance.py` 新场景 |
| V2 | 禁用 `message`、`conversations_send`、`conversations_turn`、`conversations_list` 后，私聊和群里 @ 的自动回复照常 | 同上 |
| V3 | 插件补丁在 `deliver` 失败时调用 `enqueueSystemEvent`：事件出现在该会话下一轮的什么位置；会不会自己触发一轮 | 打补丁的插件 + 假模型服务记录请求 |
| V4 | `openclaw agent --session-key … --message …` 在接入包容器里能跑；渠道会话的键怎么写；与正在处理的收消息同时发生时怎样 | 容器里运行，看假模型收到的上下文 |
| V5 | OpenIM 对象存储分片上传 50 MB 文件；实例经接入入口取文件、视频 | `ObjectStore.upload`（图片已核验）+ 扩展后的 `/im/obj/m/{id}` |
| V6 | 内联附件能经接入入口送到 `/mcp`：接入入口给实例的路由请求体上限是 1 MB（`infra/caddy/agent.Caddyfile` 的 `agent_routes`），512 KB 的附件 base64 后约 700 KB | 实测 |

## 6. 实施时的核对记录

### 6.1 openclaw-channel 补丁（T-0120，2026-11-06）

在锁定版本上核对（本机 `npm pack @openim/openclaw-channel@0.3.3`）：

| 项目 | 结果 |
|---|---|
| 发布包 sha256 | `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`，与 `docs/versions.md` 一致 |
| `dist/inbound.js` 原文件 sha256 | `5beb827deeb5cae9293fc7f363b97ba6a57bb548baf410039c544d4fa00a6cfc` |
| 补丁位置 | `processInboundMessage` 里 `dispatcherOptions.deliver` 的 `catch (e)`（原来只记 `[openim] deliver failed`）；`route`、`sessionKey`、`group` 都在这个闭包里，`api.runtime` 就是插件运行时 |
| 打补丁后的 sha256 | `36e2670f72a66ff95ea672e5456e61d0f3101384d26e12dabda489f07c9ce67d`；`node --check` 通过；第二次运行只核对、不改 |
| `enqueueSystemEvent` | OpenClaw 2026.9.36 镜像里 `/app/docs/plugins/sdk-runtime/state-and-system.md`：`api.runtime.system.enqueueSystemEvent(text, options)` 返回是否接受，带上解析好的 `agentId` 与 `sessionKey` 才走插件运行时的生命周期检查 |
| 插件完整性检查 | 同一镜像 `/app/docs/plugins/manage-plugins.md`：能力审查只对声明的能力做哈希，不对可执行文件；本地路径安装没有记录的制品完整性。所以装好后在原地改 `dist/inbound.js` 不会触发重新审查；端到端验收里实测 |

补丁做法：照旧安装核对过的原始 tarball，然后运行接入包的 `bin/channel-patch.mjs`：
原文件 sha256 不对就不打（退出码 7），结果 sha256 不对就不写（8），已经打过只核对。
新部署和已经部署的实例（插件已装、只差补丁）走同一步；SSH 重新部署时换了新凭证，`docker compose up -d` 会重建容器，补丁随之生效。

V2（禁用发消息工具后自动回复照常）、V3（系统事件进下一轮）由 `tests/e2e/agents_acceptance.py` 场景 2、3、11、20 在 CI 的 agents 任务里实测，结果补在下面。

实测结果（PR 的 CI agents 任务，提交 84f4359，2026-11-06，真实 OpenIM v3.8.3-patch.18 + OpenClaw 2026.9.36 + 打补丁的 openclaw-channel 0.3.3，73 项全部通过）：

| # | 结果 |
|---|---|
| 补丁 | 甲、乙的插件都打上补丁，重复运行只核对不改（`plugin_patch_applied` / `plugin_patch_already_applied`）；实例上报的插件版本是 `0.3.3+myworkspace.1`，实例页不再提示重新签发 |
| V2 | 禁用 `message` 等四个工具后，模型拿到的工具只有 `sessions_yield`、`tool_call`、`tool_describe`、`tool_search`（工作台工具经 Tool Search）；私聊回复（场景 2）、群里 @ 才回复（场景 3）、实例之间私聊被拒（场景 11）都照常 |
| V3 | 自动回复被每分钟上限拒收（5002）后，下一轮模型的上下文里出现补丁放回的系统事件：`System: [… UTC] [工作台] 你刚才在这个群聊里的回复没有发出（5002）：你在这个群里每分钟的条数已达上限。工作台会在限额恢复后补发这条回复（最多等 2 分钟），不要重复发同样的内容；…`；它没有自己触发一轮，而是进了同一个会话的下一轮；工作台在限额恢复后以甲的身份补发了这条回复（场景 20） |

### 6.2 终止正在执行的任务（T-0126，2026-11-06）

在锁定镜像 `openclaw/openclaw:2026.9.36`（`70c40e4`）里核对：

| 项目 | 结果 |
|---|---|
| 调网关的方式 | `node openclaw.mjs gateway call <method> --params <json> --json`（`openclaw gateway call --help`：`--params` 是 JSON 对象，默认超时 10 秒，`--json` 输出 JSON）；在容器里调本机网关，网关令牌不离开容器 |
| 找正在运行的会话 | `/app/docs/gateway/protocol/rpc-bootstrap-and-events.md`：`sessions.list` 接受 `activeOnly: true`，只选当前正在运行或排队的会话（看运行时，不看存储的状态）；`global` / `unknown` 这两种会话行带着原始键和 `agentId`，要按 agent、key 一起认 |
| 中止 | `/app/docs/gateway/protocol/rpc-session-control.md`：`sessions.abort` 传 `key`（可选 `runId`）中止该会话正在进行的工作；只传 key 的非 global 请求加 `clearQueued: true` 同时清掉它排队的后续消息；字面量 `global` 按 `chat.abort` 的归属规则处理，不做排队清理 |
| 参数结构 | `dist` 里 `SessionsAbortParamsSchema` 是封闭对象：`key`、`runId`、`agentId`、`clearQueued` 都可选，别的字段拒收。所以 `global` / `unknown` 行传 `{key, agentId}`，其余传 `{key, clearQueued: true}` |
| 一页的上限 | `activeOnly` 仍受分页限制；一个实例只跑一个 agent，正在运行的会话远少于一页，接入包只取第一页 |

做法：工作台连不进实例的网关（只监听本机），所以分两层。软停止：下令后 5 分钟内这个实例调用工作台的任何 MCP 工具都返回 `RUN_STOPPED`
（说明是谁、为什么叫停），深陷的那一轮拿不到任何工具结果；硬停止：接入包每分钟运行 `bin/stop-runs.mjs` 领取终止令，
对 `sessions.list({activeOnly: true})` 列出的会话逐个 `sessions.abort`，报回中止了几个，成功后解除软停止。
10 分钟没被领取记为过期（实例没更新接入包或不在线，只有软停止生效），领取后 5 分钟没有结果记为失败。

真实容器里的 `sessions.list` / `sessions.abort`（乙在一件事上反复调用工具，甲叫停后 `stop-runs.mjs` 中止至少一个会话、模型调用停下）
由 `tests/e2e/agents_acceptance.py` 场景 22 在 CI 的 agents 任务里实测，结果补在下面。

实测结果（PR 的 CI agents 任务，提交 22dff3a，2026-11-06，真实 OpenIM v3.8.3-patch.18 + OpenClaw 2026.9.36，场景 22 的 9 项与其余场景全部通过）：

| # | 结果 |
|---|---|
| 开关 | 没开权限时甲调用 `list_agent_runs` 返回 `FORBIDDEN_CAPABILITY`；打开后看得到乙（`is_you` 只标在自己身上） |
| 智能体叫停 | 甲用 `stop_agent_run` 叫停乙，直接执行、返回 `stopping`；乙接下来的工具调用返回 `RUN_STOPPED`；乙的 `stop-runs.mjs` 领取终止令、调网关后报回 `done`（乙当时没有正在运行的会话，`aborted` 为 0），软停止随之解除；管理员收到“甲叫停了乙”的通知 |
| 管理员叫停 | 甲在一件事上反复调用 `knowledge_search`，管理员叫停后它的下一次工具调用拿到 `RUN_STOPPED` 和原因；甲的 `stop-runs.mjs` 在容器里用 `sessions.list({activeOnly: true})` 找到正在运行的会话、`sessions.abort` 中止（`done`，`aborted` 为 1，领取到报回约 6 秒）；之后甲不再调用模型（模型调用次数 66 → 66，那一轮停下，排队的也清掉）；报回成功后解除软停止，甲的工具调用恢复 |
| 未核对 → 已查明（T-0125，见 6.4） | 乙（外网实例，public 模式）的模型经 Tool Search 调工作台工具时 OpenClaw 报 `Unknown tool id`（提交 3e9b6d5 的那次 CI），调用没到工作台；与终止无关。原因是 OpenClaw 的 SSRF 防护拦下了外网模式的 MCP 地址，见 6.4 |

### 6.3 唤醒（T-0121，2026-11-06）

V4 实测结果（同一次 CI，提交 84f4359）：接入包的 `wakeup.mjs` 在甲的容器里领取管理员建的唤醒，用
`openclaw agent --session-key openim:group:<群 ID 小写> --message-file … --timeout 600 --json` 跑完一轮并报回 `done`；
这一轮在群的会话里运行（模型收到的上下文里有群里之前的消息，消息条数 2 → 4），甲按提示词用 `im_send` 在群里发了言（场景 21）。
会话键写法与 openclaw-channel 的 `openim:group:<gid>` / `openim:<sendID>`（小写）一致。

### 6.4 附件、定时、断线排队与外网模式的 MCP（T-0125，2026-11-06）

V1、V5、V6 实测结果（PR brianabc121/WorkspaceCore#161 的 CI agents 任务，提交 6d78f7a，真实 OpenIM v3.8.3-patch.18 + OpenClaw 2026.9.36，场景 24 全部通过）：

| # | 结果 |
|---|---|
| V1 | 甲打开附件权限后经 `im_send` 一次发文字 + 图片 + 文件 + 视频，`status = sent`；管理员的会话里按顺序收到 `text`、`image`、`file`、`video` 四条（真实 OpenIM 接受实例身份发出的 102 / 105 / 104）。没打开附件权限时 `ATTACHMENT_FORBIDDEN` |
| V5 | 文件与视频存进 OpenIM 对象存储（Garage）：管理员按附件下载文件，内容与发出时一致、`Content-Disposition: attachment`；视频读出时长 3 秒并带默认封面，`Range: bytes=0-15` 返回 206 与 16 字节。50 MB 的大文件没有在 CI 里实测（分片上传用的是图片已核验的同一个 `ObjectStore.upload`） |
| V6 | 内联附件（`data_base64` 与 `text_content`）经实例的 MCP 请求送到工作台并发出；执行记录里只有长度（`不记录`），没有内容 |
| 定时 | `send_at` 在 75 秒后：返回 `scheduled`，到点前会话里没有，到点后 Worker 发出一条 |
| 断线排队 | 停掉 openim-server 时 `im_send` 返回 `queued`；启动后工作台补发，30 秒后会话里仍只有一条（不重复）；之后甲、乙重新上线 |

Issues 与 README 的实测结果（同一 PR 的 CI agents 任务，提交 9697797，场景 25 全部通过）：

| 项 | 结果 |
|---|---|
| README | 项目 README 自动列出成员甲、乙；甲经 `readme_update` 写进展与交接，直接生效；README 的沟通部分自动列出未关闭的 Issue |
| Issue 唤醒与评论 | 接口约定 Issue 指派给甲（内网模式）→ 工作台排 Issue 唤醒 → `wakeup.mjs` 领到并叫醒甲，甲经真实 OpenClaw 的模型调 `issue_comment` 评论 |
| 连续评论暂停 | 加上被唤醒时的那条，甲、乙交替评论满 10 条后，第 11 条返回 `ISSUE_PAUSED`；管理员评论后恢复 |
| 阻塞 Issue | 未关闭的阻塞 Issue 让 `complete_task` 返回 `TASK_BLOCKED`；关闭后甲能完成 |
| 浏览器验收 | `tests/e2e/readme_issues_browser.cjs` 在 CI 的 compose 任务里 8 项通过（README 角标、页面开 Issue、沟通部分列出与撤下、评论、关闭、手机宽度、viewer 只读） |

**外网模式实例的模型用不了工作台的 MCP 工具（已查明，未修复，见任务 T-0133）**：

- 现象：乙（外网模式）的 OpenClaw 日志 `[security] blocked URL fetch (mcp-http) targetOrigin=http://public.test:18443 reason=Blocked: resolves to private/internal/special-use IP address`，
  接着 `[bundle-mcp] failed to start server "myworkspace"`，模型调 `myworkspace__*` 得到 `Unknown tool id`。场景 25 第一次（提交 6d78f7a）因此失败；之前的场景对乙都是直接调 MCP、不经模型，所以没暴露。
- 原因（锁定镜像里的代码 `dist/mcp-http-fetch-*.mjs`、`dist/ssrf-*.mjs`）：MCP 的 HTTP 请求经 `fetchWithSsrFGuard`，配置的 MCP 地址的主机名被信任（`allowedOrigins`），
  解析到私有地址（10 / 172.16 / 192.168）放行——所以内网模式的甲正常；但解析到回环地址（127.0.0.1）时，除非主机名本身是 `localhost`、`*.localhost` 或回环 IP，一律拦下
  （`assertAllowedTrustedHostnameResolvedAddressesOrThrow`）。外网模式按设计把工作台外网域名在容器里指向 127.0.0.1（本机隧道），正好被拦。
- 不能只把地址改成 `http://127.0.0.1:<端口>/mcp`：外网入口的 Caddy 按主机名分站点且开了双向证书，要求 Host 与 SNI 一致；
  在锁定镜像里实测 undici 8.10.2 的 `fetch` 会忽略自定义的 `Host` 头（发出去的仍是 `127.0.0.1:<端口>`）。
- 影响：外网模式实例收发 IM 正常（openclaw-channel 不走这个检查），但模型调不到任何工作台工具（任务、知识库、IM 发消息、Issue、邮件……）。内网模式不受影响。
