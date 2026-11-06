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
