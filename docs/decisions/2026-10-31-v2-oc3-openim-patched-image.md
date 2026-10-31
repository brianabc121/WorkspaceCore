# V2 OC-3：打过补丁的 OpenIM Server 镜像

- 状态：accepted
- 日期：2026-10-31
- 作者：claude（协作任务 T-0055，OC-3 第 1 部分）
- 依据：`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md` 第 4、6 节（人类 2026-10-31 同意自建补丁镜像，不向上游报告）

## 1. 锁定的输入

| 输入 | 版本 | 校验 |
|---|---|---|
| 上游源码 | `openimsdk/open-im-server` v3.8.3-patch.18 | 提交 `120cee6d5783959a02ea1acfa414589fe5aae90f`，`build.sh` 不一致就退出 |
| 构建镜像 | `golang:1.26.9-alpine3.24`（2026-10-10） | `sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade` |
| 运行镜像 | `alpine:3.24.2`（2026-09-18） | `sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6` |
| mage | v1.17.2（2026-04-22） | Go 模块校验（sum.golang.org） |
| Go 依赖 | 上游 `go.sum` | `go mod verify` |

- Go 工具链用 1.26.9：上游 `go.mod` 要求 ≥ 1.25.0。Go 只支持最新的两个大版本，1.27 发布后 1.25 已不在支持期内；1.26 和上游要求的版本差距较小，所以选 1.26。
- 上游 Dockerfile 用的是 `golang:alpine`、`alpine:latest`、`mage@latest`，全是浮动版本，所以不用它，改用 `infra/openim/server/Dockerfile`。

## 2. 补丁（`infra/openim/patches/`，`git am` 顺序应用）

| 补丁 | 位置 | 做法 |
|---|---|---|
| 0001 | 消息网关 `internal/msggateway` | 1001 / 1002 / 1005 / 1006 / 1007 / 2003 的内层请求，用户字段一律换成连接的用户；1003 发消息要求内层发送人等于连接的用户，并且不接受客户端发通知类会话（4）或通知类内容（≥ 1000）；1004 信令直接拒绝（上游把空请求转给 RPC，客户端用不到） |
| 0002 | 消息 RPC `internal/rpc/msg` | `PullMessageBySeqs`、`GetSeqMessage`、`GetLastMessage`、`GetConversationsHasReadAndMaxSeq`、`SetConversationHasReadSeq`、`MarkMsgsAsRead`、`MarkConversationAsRead` 要求调用者就是请求里的用户（或管理员）；`SearchMessage` 只给管理员 |
| 0003 | REST 层 `internal/api/conversation.go` | 会话接口检查请求里的属主就是调用者（管理员除外）；`get_conversation_offline_push_user_ids` 只给管理员 |

### 为什么会话的检查放在 REST 层，而不是 RPC

ADR 第 6 节原计划在会话 RPC 里校验调用者。核对源码后发现，会话 RPC 被其他服务**代表别的用户**调用：
- 发消息时，消息服务读取收件人的会话设置；
- 群服务给全体成员改会话。

这些调用里，调用者和请求里的用户本来就不同。在 RPC 里校验会把这些正常流程一起拦掉。所以改在只有外部请求才会经过的 REST 层校验。

消息 RPC 的那几个读取和已读接口，只有 REST 层、消息网关和 JS SDK 接口会调用，它们都以调用者本人身份调用，所以在 RPC 里校验是安全的。

### 补 OC-0 没发现的问题

核对消息网关时发现，1003 发消息同样只校验外层信封，**内层的发送人没有校验**。补丁 0001 一并修复，并附带单元测试 `TestCheckClientMsg`，构建时运行。

OC-0 的回归测试脚本没有覆盖这一项，覆盖它的是这个单元测试。

## 3. 镜像

- 由 `infra/openim/server/build.sh` 在本机或 CI 上构建，镜像名 `my-workspace/openim-server:v3.8.3-patch.18-myws.1`，不推送到镜像仓库。`compose.yaml` 写 `pull_policy: never`，所以不会意外拉到别的镜像。
- **偏差**：方案写的是“锁定 digest”。自建镜像每次构建的 digest 会不同（文件时间戳不同），所以锁定的是**全部构建输入**（第 1 节）。
- 以用户 10001 运行；只有 `_output` 下的日志与临时目录可写。
- 构建参数：
  - `RELEASE=true`：去掉调试符号；
  - `BASE_REGISTRY`：Docker Hub 限流时换成同一 digest 的镜像站；
  - `proxy_ca`：TLS 拦截代理的 CA，作为 BuildKit secret 传入，只在下载依赖时使用，不进镜像。

## 4. 回归测试与本机结果

- `infra/openim/probes/rest-authz.py`（OC-0 提交的脚本，未修改）对补丁镜像必须以 0 退出。
- CI 工作流 `ci-openim` 在 `infra/openim/**` 变化时运行：构建镜像 → 用 `compose.yaml` 加 `compose.probe.yaml` 启动（多一个放行一切的回调桩）→ 运行测试脚本。

本机结果（2026-10-31，本会话自己的 Docker，全新数据卷）：

| 项目 | 结果 |
|---|---|
| 构建 | 成功；镜像 1.39 GB（官方 2.17 GB）；`TestCheckClientMsg` 通过 |
| 启动 | 以用户 10001 运行，约 25 秒后 `get_admin_token` 返回 errCode 0 |
| `rest-authz.py` | 5 项全部“已挡住”，退出码 **0**（官方镜像是 1） |
| 正常使用 | 用户以自己的令牌：拉取自己的会话（能读到消息）、增量同步会话、读取免打扰列表、读写自己的会话设置、读取已读序号、标记已读、读取好友列表，全部 errCode 0；管理员令牌：搜索消息、读写任意用户的会话，全部 errCode 0 |
| 空闲内存 | openim-server 377 MiB、kafka 388 MiB、mongo 286 MiB、minio 91 MiB、etcd 16 MiB、redis 4 MiB |

## 5. 依赖漏洞（trivy 0.76.1，只看已有修复的高危 / 严重）

- 严重 0；高危按二进制累计 222 条，去重后是上游 `go.mod` 锁定的依赖：
  - `golang.org/x/{crypto,net,text,image}`、`github.com/klauspost/compress`；
  - `google.golang.org/grpc` 1.79.3、`go.opentelemetry.io/otel` 1.39.0；
  - Go 标准库 1.26.9（修复在 1.26.10，2026-10-27 发布，不满两周，这次不用）。
- 都不是补丁引入的。升级 grpc / otel 这类依赖要做兼容性验证，所以和替换 bitnamilegacy 一起放到 OC-3 的下一部分：
  - 第 4 个补丁升级依赖；
  - 镜像扫描加入 `ci-openim`。

## 6. 还没做的（T-0055 后续部分）

- 升级第 5 节的依赖，镜像扫描进入 CI；
- 替换 bitnamilegacy 的 kafka / minio / etcd，开启 etcd 认证和 Kafka SASL，调小 Kafka 日志段；
- 接入工作台：内部网络、回调地址、接入入口 `/im/*`、`.env.example`；
- 备份 MongoDB 与 MinIO。
