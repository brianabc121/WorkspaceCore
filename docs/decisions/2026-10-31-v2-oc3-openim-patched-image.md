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

- ~~升级第 5 节的依赖，镜像扫描进入 CI~~（第 2 部分，见第 7 节）；
- ~~替换 bitnamilegacy 的 kafka / etcd，开启 etcd 认证和 Kafka SASL，调小 Kafka 日志段~~（第 2 部分）；
- ~~替换 bitnamilegacy 的 minio~~（第 3 部分，见第 8 节：换成 Garage）；
- 接入工作台：内部网络、回调地址、接入入口 `/im/*`、`.env.example`；
- 备份 MongoDB 与 MinIO。

## 7. 第 2 部分：依赖升级、etcd 与 Kafka 换官方镜像并开启认证（2026-10-31）

镜像名改为 `my-workspace/openim-server:v3.8.3-patch.18-myws.2`（内容变了，标签跟着变）。

### 7.1 补丁 0004：依赖升级

| 依赖 | 原版本 | 新版本 | 原因 |
|---|---|---|---|
| `golang.org/x/net` | 0.55.0 | 0.60.0（2026-10-08） | 高危，已有修复；要求 Go ≥ 1.26 |
| `golang.org/x/image` | 0.39.0 | 0.45.0 | 高危 |
| `github.com/klauspost/compress` | 1.17.7 | 1.18.7 | 高危 |
| `google.golang.org/grpc` | 1.79.3 | 1.83.2（2026-08-25） | 高危（三项） |
| `github.com/go-jose/go-jose/v4` | 4.1.4 | 4.1.6（2026-10-14） | 升级 grpc 后带进来的，高危 |
| `golang.org/x/crypto`、`x/text`、`otel` | 0.52 / 0.37 / 1.39 | 0.57 / 0.42 / 1.44 | 上面几项要求的最低版本 |

- `go.mod` 的 `go` 指令随之变成 1.26.0（构建镜像是 1.26.9）；连带升级了 Firebase 推送用到的 `cloud.google.com/go`、`google.golang.org/api` 等间接依赖。
- `go build ./...`、`TestCheckClientMsg` 通过。`go vet` 报的无键结构体字面量和一个测试文件的未知字段，升级前就有（上游问题）。
- trivy：严重 0；高危只剩 Go 标准库 1.26.9。修复在 1.26.10，2026-10-27 发布，不满两周，下次升级构建镜像时处理。
- `ci-openim` 增加镜像扫描：高危只报告，有修复的严重漏洞就失败。

### 7.2 etcd：官方镜像 + 认证

- 镜像 `gcr.io/etcd-development/etcd:v3.5.34@sha256:04726053dabaf26b56ce7ef28c3d20b8b4a4006ed887d30194aa4f0756ee4f8d`（2026-09-22；OpenIM 的客户端是 etcd 3.5 系列）。distroless 镜像，没有 shell。
- 认证由一次性服务 `etcd-init`（`alpine:3.24.2`，脚本 `infra/openim/etcd-init.sh`）通过 etcd 的 JSON 网关开启：
  - 两个用户：`root`（角色 root）与 `openim`；
  - `openim` 只能读写 `openim` 前缀和 `/check_openim_component` 这一个键。后者是启动检查 `check-component` 用的，在前缀之外，第一次实测时因为没授权而启动失败；
  - 已经开启认证时，每次启动重新授权，并同步 `openim` 的密码（支持轮换）。
- 实测：
  - 不带凭证读写 `openim` 前缀返回 400；
  - `openim` 用户写前缀之外的键返回 403；
  - OpenIM 用 `openim` 用户正常启动。

### 7.3 Kafka：官方镜像 + SASL/PLAIN

- 镜像 `apache/kafka:3.9.2@sha256:05b4616e0702ef2729327705d54ad6b50ea70b271c4b730fabd2320789fb7b02`（2026-02-21），KRaft 单节点。
- **不用 4.x**：OpenIM 的消费者组按 Kafka 2.0 协议连接（`openimsdk/tools` 的 `mq/kafka/config.go` 写死 `sarama.V2_0_0_0`），而 Kafka 4.0 删掉了 2.1 以下的协议版本（KIP-896）。3.9 是最后一个保留旧协议的系列。
- broker 与 controller 两个监听都是 `SASL_PLAINTEXT` + PLAIN，只有一个用户 `openim`。OpenIM 用 `IMENV_KAFKA_USERNAME` / `IMENV_KAFKA_PASSWORD` 连接（sarama 默认机制就是 PLAIN）。
- 日志段 16 MiB、保留 72 小时、索引上限 1 MiB、堆 512 MiB：
  - 空闲时数据卷 648 KB（bitnamilegacy 时是 1.74 GB），内存约 370 MiB；
  - 消息由 OpenIM 写进 MongoDB 后，Kafka 里的只是中转。
- 实测：
  - 不带 SASL 的客户端被拒（broker 日志 `Failed authentication ... Unexpected Kafka request of type METADATA during SASL handshake`）；
  - 带 SASL 能列出 OpenIM 的 4 个主题；
  - 管理员代发的消息能被收件人拉到，说明消息经 Kafka 中转正常。
- 数据卷改名为 `openim-kafka-data` / `openim-etcd-data`：官方镜像的数据目录和属主都变了，不复用旧卷。

### 7.4 MinIO：这次没换

- Docker Hub 上的 `minio/minio` 查不到 tag 信息；`quay.io` 被本会话的代理拒绝（403）；`bitnami/minio` 已经是空的。
- 候选：官方 MinIO（需先核实发布状态）、Garage、SeaweedFS、RustFS。
- OpenIM 的对象存储有 minio / aws / cos / oss / kodo 几种驱动，换实现之前要先核对它用到的 S3 接口（例如公开读的 bucket policy）。所以和图片上传一起做。

### 7.5 本机结果（全新数据卷）

| 项目 | 结果 |
|---|---|
| `rest-authz.py` | 5 项全部“已挡住”，退出码 0 |
| 正常使用 | 拉取自己的会话（读到消息）、增量同步会话、改自己的会话设置、标记已读、好友列表、管理员搜索消息，全部 errCode 0 |
| 空闲内存 | openim-server 395 MiB、kafka 370 MiB、mongo 354 MiB、minio 125 MiB、etcd 18 MiB |

## 8. 第 3 部分：对象存储换成 Garage（2026-11-01）

### 8.1 为什么换、换成什么

- MinIO 已经不能用锁定的官方镜像了：
  - Docker Hub 上的 `minio/minio` 仓库已不存在（API 返回 `object not found`，pull 报 `repository does not exist`）；
  - `quay.io` 被本会话的代理拒绝（403），无法核实；
  - `bitnami/minio` 已经是空的，`bitnamilegacy/minio:2024.1.13` 不再更新。
- OpenIM 的 minio 驱动（`openimsdk/tools` 的 `s3/minio`）只用标准 S3 接口，所以任何兼容 S3 的服务都可以：
  - 桶：BucketExists、GetBucketLocation、MakeBucket；
  - 对象：Get、Put、Stat、Remove、ListObjects；
  - 分片：New、ListParts、Complete、Abort MultipartUpload；
  - 预签名 URL；
  - `SetBucketPolicy` 只在 `publicRead: true` 时调用，我们保持 false。
- 但上传流程（`s3/cont`）有两个硬要求：单次 PUT 的 ETag 必须是内容的 MD5；CopyObject 后 ETag 不变。
- 候选：

  | 候选 | 版本 | 镜像 | 许可证 | 结论 |
  |---|---|---|---|---|
  | **Garage** | v2.4.1（2026-09-08） | 99 MB，单个静态程序 | AGPL-3.0（人类已接受 AGPL） | **采用**：实测通过，空闲内存 9 MiB |
  | SeaweedFS | 4.48（2026-09-28） | 724 MB | Apache-2.0 | 没有实测；镜像大，凭证要从环境变量生成 JSON 配置 |
  | RustFS | 1.0.x（1.0.0 是 2026-09-16） | — | Apache-2.0 | 太新，没有选 |

- 镜像 `dxflrs/garage:v2.4.1@sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020`。

### 8.2 部署

- `infra/openim/garage.toml`：
  - 单节点、单副本，元数据用 sqlite，S3 区域 `garage`；
  - 管理 API 只在 openim 内部网络；
  - **监听 `0.0.0.0`**：本会话的 Docker 没有 IPv6，Garage 示例里的 `[::]` 会启动失败（`Address family not supported by protocol`）。
- 一次性服务 `garage-init`（`alpine:3.24.2` + `garage-init.sh`，调用管理 API v2），幂等：
  - 布局还是 0 版时，加入本节点并应用。重复应用会报 `Invalid new layout version`，所以只在 0 版时做；
  - 访问密钥不存在（404）就 `ImportKey`；存在时比对密钥，不一致就报错退出。Garage **不允许复用已删除的密钥 ID**（`KeyAlreadyExists ... Even if it is deleted`），所以轮换时要换一个新的 `GK…` ID；
  - 桶 `openim` 不存在（404）就创建；授权读、写、owner（重复授权返回 200）。
- 变量：`OPENIM_GARAGE_RPC_SECRET`（64 位十六进制）、`OPENIM_GARAGE_ADMIN_TOKEN`、`OPENIM_S3_ACCESS_KEY`（`GK` + 24 位十六进制）、`OPENIM_S3_SECRET_KEY`（64 位十六进制）。删掉了 `OPENIM_MINIO_SECRET`。
- OpenIM 仍用 minio 驱动：
  - 内部地址 `garage:3900`；
  - 外部地址 `OPENIM_OBJECT_EXTERNAL_URL`。它出现在预签名链接里，接入工作台时改成接入入口的 `/im/obj`。

### 8.3 实测（本会话自己的 Docker，全新数据卷）

| 项目 | MinIO（bitnamilegacy，对照） | Garage |
|---|---|---|
| 200 KB 文件，预签名单次上传 | ETag = MD5，下载一致 | ETag = MD5，下载一致 |
| 12 MB 文件，3 片分片上传 | 每片 ETag = MD5，下载一致 | 每片 ETag = MD5，下载一致 |
| PNG 缩略图（`access_url` 加 `type=image`） | — | 400×300 → 64×48 PNG |
| 匿名访问桶和对象 | — | 403 |
| `garage-init` 重跑 | — | 只输出“Garage 就绪”，不重复建 |
| 密钥不一致 | — | 退出码 1，提示换新的密钥 ID |
| `rest-authz.py` | 0 | 0 |
| 空闲内存 | 125 MiB | 9 MiB |

- `infra/openim/probes/object-check.py` 覆盖前三项，进入 `ci-openim`（在 openim 网络的容器里运行，因为预签名链接要能访问到）。
- 实测中遇到过一次：openim-server 容器是在 10002 端口被占时创建的，启动后 DNS 指向了 8.8.4.4。重建容器后恢复（`127.0.0.11`）。这是本机操作顺序的问题，不是配置问题。

### 8.4 还没做的

- 预签名链接的外部地址经接入入口（`/im/obj`）提供；外链图片不加载（工作台代理显示）。这些属于接入工作台与 IM 后端部分。
- Garage 数据卷纳入 restic 备份与 A15 恢复演练。

## 9. 第 4 部分（B1）：OpenIM 连接器与核验（2026-11-01）

`services/api/app/integrations/openim.py`：工作台后端以 imAdmin 身份调用 OpenIM REST 的连接器。
- 管理员令牌缓存在 Redis，到期前 1 小时刷新；遇到令牌类错误（1501 ~ 1507）重取一次。
- 复用 `ConnectorHttp`（超时、熔断、错误归类）；OpenIM 地址由部署配置放行。

在补丁镜像上实测（`tests/test_openim_live.py`，连续 3 次通过）：

| 契约第 13 节 / 连接器依赖的行为 | 结论 |
|---|---|
| `ws_` / `oc_` 加 32 位十六进制的用户 ID（35 个字符） | 可用 |
| IM 令牌有效期能否按次指定 | **不能**：`/auth/get_user_token` 只收 `userID`、`platformID`；有效期由 `openim-rpc-auth.yml` 的 `tokenPolicy.expire`（天，默认 90）决定，接入部署时改成 30 |
| protobuf 的 StringValue / BoolValue / Int32Value 字段（群名、群公告、昵称、置顶、免打扰）怎么写 | **按普通值写**。写成 `{"value": ...}` 返回 1001：OpenIM 的 JSON 解析把包装类型当标量 |
| 重复注册 | 1102 `RegisteredAlreadyError`，归为 CONFLICT |
| `is_friend` / `import_friend` | 导入是双向的；`is_friend` 要两个方向都为真 |
| 以 `ws_` 用户代发后能否按序号拉回原文 | 能。会话里还有导入好友产生的通知（contentType 1201），所以投影要按 contentType 过滤 |
| 群：建立、邀请、改名与公告、@ 消息（106，`{"text", "atUserList"}`）、移出、解散 | 全部可用；解散后再邀请会报错 |
| 会话置顶、免打扰（`recvMsgOpt=2`）同步到 OpenIM | 可用 |

- 错误码归类：
  - 1101 / 1201 / 1203 / 1204 → NOT_FOUND；
  - 1102 / 1202 / 1304 → CONFLICT；
  - 1001 → VALIDATION_FAILED，1002 → AUTH_FAILED；
  - 其余 → UNAVAILABLE；
  - 错误信息里不带 OpenIM 的错误详情。
- 数据表（IM 投影、收件箱、群与通讯录）和用到它们的业务代码一起，放在下一部分。

## 10. 第 4 部分（B2）：IM 数据表、账号登记、IM 令牌与回调（2026-11-01）

- **数据表**（迁移 0012）：
  - `im_accounts`：管理员的 `ws_` 账号，一个用户一个；
  - `im_groups`：群与防刷屏阈值（方案第 5 节的 `im_group_policies` 与群一对一，合并进来）；
  - `im_group_members`：成员是实例或用户，二者恰好一个；
  - `im_webhook_inbox`：after 回调，按 `event_key` 去重，状态与 V1 的 `webhook_inbox` 相同。
  - 会话、消息投影、置顶、分组和上传的表，与用到它们的代码一起加。
- **账号登记**（`app/domain/im.py`）：
  - 登记实例后注册 `oc_` 账号，再与本工作空间状态正常的 owner / admin 互加好友。管理员的 `ws_` 账号第一次用到时注册并登记。
  - 全部成功才 `im_ready = true`。OpenIM 不可用时实例照常登记，`im_ready` 保持 false：
    - Worker 每 5 分钟重试一轮（`provision_im`）；
    - 实例启动时取 IM 令牌也会先补一次。
  - 重复注册（1102）当作已注册；加好友前先查关系（OC-0 F2）。
- **强制下线**：停用实例、关闭总开关后调用 `/auth/force_logout`。
  - 尽力而为，失败只记日志；回调（5004）与接入网关还会按状态拒绝。
  - 重新启用后，实例要重启才会取新令牌（旧令牌已被下线作废）。
- **`/agent-api/v1/im-token`**：
  - 每个实例每小时 10 次，`Cache-Control: no-store`；
  - 没有部署 OpenIM 或 OpenIM 不可用时返回 503 `IM_UNAVAILABLE`；
  - `expires_at` 按 OpenIM 返回的 `expireTimeSeconds` 计算。
- **回调**（`/internal/openim/<口令>/<命令>`，`app/domain/im_callbacks.py`）：
  - 没有部署 OpenIM、没配口令、口令不对（常量时间比较）、来源不在 `OPENIM_CALLBACK_ALLOWED_CIDRS` 时一律 404。来源按直接对端判断，不看 `X-Forwarded-For`。
  - before 回调按契约第 10 节判断。几个实现上的取舍：
    - 载荷无效拒收 5005；数据库或 Redis 出错、判断超过 1 秒拒收 5009；
    - 连续计数先 `INCR` 再比较，超出就 `DECR` 并拒收，避免并发的消息一起越过上限；
    - 每次暂停只在第一次拒收时通知管理员（`im_relay_paused`）；
    - 降级成 member 的用户在单聊和群里都拒收 5005。
  - after 回调写收件箱后放行。缺字段或不是 JSON 也放行，不入库，靠对账补齐。
  - `AfterImportFriends` 放行不处理（OC-0 F2）；其他命令放行，并在 Redis 哈希 `openim:callback_ignored` 里按命令计数。
- **测试**：模拟 OpenIM（`tests/fake_openim.py`）加真实 PostgreSQL / Redis，12 项（`tests/test_im_accounts_callbacks.py`）。
- **还没做的**：
  - 收件箱的处理：消息投影、上线更新实例状态并补好友、删好友后自动补回；
  - 群里由 `ws_notice` 发的“自动接力已暂停”提示：要在建群时把 `ws_notice` 拉进群，和群接口一起做；
  - 管理员后来才升为 admin 时补加好友：放到管理员第一次打开 IM 时做；
  - OpenIM 真实回调到 API 的端到端验证：接入部署（回调地址、网络）时做。

## 11. 第 4 部分（B3）：消息投影、收件箱处理与对账（2026-11-01）

- **数据表**（迁移 0013）：
  - `im_conversations`：每个管理员在一个工作空间里的会话，已读、置顶、免打扰各自独立；
  - `im_messages`：消息投影，按 OpenIM 的会话 ID 存一份，群里的多个管理员共用。
    - 唯一约束：`server_msg_id`、`(conversation_id, seq)`、`(conversation_id, client_msg_id)`；
    - 保留期 `IM_MESSAGE_RETENTION_DAYS`（默认 365 天），由每小时的清理任务删除；OpenIM 里的消息不受影响。
      方案第 4.4 节写默认 180 天；人类 2026-11-01 确认保持 365 天（T-0064）。
- **投影**（`app/domain/im_projection.py`）：
  - 回调只当作“这个会话有变化”的提示：取会话的最大序号（`/msg/newest_seq`），再按序号拉取缺的消息（`/msg/pull_msg_by_seq`，每次 100 条）。
  - 以会话成员的身份拉取：单聊用管理员，群聊优先用群主。每个会话最多补最近 1000 条。
  - 消息类型（常量取自锁定的 `openimsdk/protocol` v0.0.73-alpha.19）：
    - 101 文字、106 @ 消息、102 图片：保留解码后的内容；
    - 1201、1501、1509 等通知：生成中文说明作为系统消息，其他 1000 以上的通知统一为“系统通知”；
    - 其他类型只记类型（前端显示“暂不支持的消息”）。
  - 内容超过 64 KiB 或无法解析时不保存原始内容。
- **收件箱处理**：
  - 领取用 `FOR UPDATE SKIP LOCKED` 加 2 分钟租约；失败按指数退避重试，8 次后进 `dead_letter`。
  - 回调写入后由 API 投递作业，Worker 另有每分钟一次的兜底。
  - **上线**：实例状态改为 online，并补一次好友关系（后来才加入的管理员也能收到）。
  - **下线**：先向 OpenIM 核对实时状态（`/user/get_users_online_status`，返回的 `data` 是列表），确实不在线才改为 offline，因为同一实例可能同时有新旧两个连接。
  - **乱序**：按回调里的毫秒时间戳丢弃过期的事件。
  - **停用的实例又连上来**：再强制下线一次。
  - **删好友**：实例与本工作空间管理员之间的好友被删时立即补回，并给那个管理员发 `im_friend_restored`。
- **对账**：
  - 每 30 秒按每个管理员账号的 `newest_seq` 补齐有变化的会话，回调丢失也能补上；
  - 每天一次全量（UTC 19:23），重新拉每个会话最近 1000 条，补上中间缺的；
  - OpenIM 不可用时本轮停止，下一轮再来。
- **在真实实例上的验证**：全新数据卷的补丁镜像，`tests/test_im_live.py` 加 `tests/test_openim_live.py` 连续 3 次通过。核对了以下几项：
  - 登记实例时真实注册并互加好友；
  - 取到真实的 IM 令牌；
  - 以实例身份代发的消息经对账投影出来，序号、`serverMsgID`、发送人都对得上；
  - 导入好友的通知按系统消息投影；
  - 在线状态接口的列表格式；
  - 停用时强制下线。
- **测试**：模拟 OpenIM 加真实数据库共 17 项（`tests/test_im_projection.py`），包括解析、回调触发投影、群、对账补缺、上下线、删好友补回、重试与 dead_letter、清理。

## 12. 第 4 部分（B4a）：管理员 IM 接口——会话、消息、发送、置顶、导出（2026-11-01）

实现 `app/api/v1/im.py` 中会话与消息相关的接口（`app/domain/im_service.py`），契约第 5.1–5.3、5.5、5.8 节。
- **数据表**（迁移 0014）：
  - `im_message_pins`：挂在当前用户的会话上；
  - `im_messages.idempotency_key`：唯一，可空。
- **状态**（`GET /im/status`）：第一次打开 IM 时注册当前用户的账号，并与本工作空间已就绪的实例补好友（后来才成为管理员的用户也能聊天）。OpenIM 不可用时 `available=false`。
- **角标**：未读数是序号大于已读序号、不是自己发的消息，不含免打扰的会话。待审批数在 OC-5 实现，暂为 0。
- **会话**：
  - 排序：置顶的在前（`pinned_at` 倒序），其余按最后消息时间倒序；
  - 筛选：`kind`、按标题（实例昵称或名称、群名）的关键字；`contact_group_id` 在通讯录分组实现前一律 404；
  - 打开单聊：实例 IM 未就绪时 409 `IM_NOT_READY`，顺带补好友；
  - 置顶与免打扰：先同步到 OpenIM（`isPinned`、`recvMsgOpt=2`），失败时 503、本地不改；
  - 已读：只增不减，不超过最大序号。
- **消息列表**：按 `sent_at` 倒序，游标是 (`sent_at`, `id`)。类型映射：
  - 101 / 106 → `text`；102 → `image`；1000 以上 → `system`；其他 → `unsupported`；
  - 图片的 `media_id` 等对象存储代理做完再给出。
- **发送**：
  - `Idempotency-Key` 沿用 V1 的幂等记录（同一个键换内容 409）；
  - 先写一条 `sending` 的本地消息再调用 OpenIM。`clientMsgID` 由 OpenIM 服务端生成、调用方不能指定（`internal/api/msg.go` 已核对），所以把键写进 `ex`（原样存进 MsgData，真实实例验证过能随 `pull_msg_by_seq` 带回）；
  - 结果按错误分三类：

| OpenIM 的结果 | 接口返回 | 幂等记录 |
|---|---|---|
| 拒收：before 回调的 5001–5009 原样作为 `errCode` 返回（`pkg/callbackstruct/common.go` 已核对），以及 1001 等参数错误 | 409 `IM_SEND_REJECTED`（`details.err_code`） | 记为失败，重放同样的错误 |
| 确定没发出：连不上、熔断、管理员令牌被拒 | 503 `IM_UNAVAILABLE` | 删除，同一个键可以重试 |
| 结果未知：超时、5xx | 504 `RESULT_UNKNOWN` | 记为未知 |

  - 结果未知后用同一个键重发：先补齐那个会话的投影，按 `ex` 里的键找回那条消息（投影会认领本地那一行，不会出现两行）；找到就返回 202，5 分钟后仍找不到就返回 409 `OPERATION_NOT_APPLIED`；
  - 单聊不能 @，群里只能 @ 群成员；每人每分钟 30 条。
- **消息置顶**：每个会话最多 10 条（先锁住会话行再数）；重复置顶返回原来那条。
- **导出**：NDJSON，按序号升序分批输出，`Cache-Control: no-store`，写审计 `im.conversation.exported`。
- **测试**：
  - `tests/test_im_api.py`：9 项，覆盖权限、会话归属、排序与筛选、同步失败不改本地、发送的各种结果、限流、分页、置顶上限、导出；
  - `tests/test_im_live.py`：在真实补丁镜像上，管理员发送后 `ex` 原样带回、投影只有一行，连续 3 次通过。

## 13. 第 4 部分（B4b）：群、通讯录与分组（2026-11-01）

实现 `app/api/v1/im.py` 中群、通讯录与分组的接口（`app/domain/im_groups.py`），契约第 5.6、5.7、10 节。
- **数据表**（迁移 0015）：`im_contact_groups`（名称在工作空间内唯一）、`im_contact_group_members`。
- **建群**：
  - 群主是当前用户；成员只能是本工作空间已就绪、没有停用的实例（不存在 404，未就绪 409 `IM_NOT_READY`）和状态正常的 owner / admin（其他一律 404）；
  - `ws_notice` 一起拉进群。调用者 imAdmin **不入群**：CreateGroup 把 `opUserID` 加进的只是用于核对用户和计算成员数的列表，真正入群的只有群主、管理员和成员（第 14 节在真实实例上核实，更正了这里最初的判断）；
  - 群公告随建群写入（`groupInfo.notification`）。
- **建群的幂等**：OpenIM 允许调用方指定群 ID，已存在时返回 1202（同一文件的 GenGroupID）。所以群 ID 由 Idempotency-Key 推出（`ws` 加 32 位十六进制）：
  - 重发同一个键时遇到 1202 就当作上次已经建好；
  - 结果未知（超时）返回 504，其他失败返回 503 并删除幂等记录；两种情况都可以直接用同一个键重发，不会多建群。
- **改名与公告、邀请、移出、解散**：
  - 先调用 OpenIM，失败时 503、本地不改；
  - 不能移出群主（422）；被移出的管理员同时删掉这个群的会话；
  - 只有群主能解散（403），解散过的再解散直接成功，之后改群返回 409。
- **防刷屏设置**：乐观并发，版本号与群资料共用；`paused` 取 Redis 里的实时连续条数。
- **自动接力暂停的提示**：第一次拒收 5001 时，除了给管理员发 `im_relay_paused` 通知，还由 `ws_notice` 在群里发一条提示，补上第 10 节里 B2 留下的部分。
- **通讯录**：本工作空间的全部实例，停用的显示为 disabled（没有删除好友）；可以按分组、关键字筛选，并给出与当前用户的单聊会话 ID。
- **分组**：
  - 名称重复返回 409 `CONTACT_GROUP_EXISTS`（OC-3 补充，已写入契约第 12 节与 `docs/api-contract.md`）；
  - 成员整体替换，别的工作空间的实例返回 404；删除分组不影响实例；
  - 会话列表的 `contact_group_id` 筛选现在可用。
- **验证范围**：
  - 模拟 OpenIM 加真实数据库共 5 项（`tests/test_im_groups_api.py`）；
  - 建群、邀请、移出、改名与公告、解散在 B1 的真实实例测试里验证过；
  - 指定群 ID 与随建群写入公告这两点，目前只按锁定源码核对过：本会话的 Docker 守护进程已到时限停止，没能在真实实例上再跑。接入部署时一并验证。

## 14. 第 4 部分（B5）：图片，以及 B4b 的真实实例核验（2026-11-01）

### 14.1 B4b 补验

用全新的补丁镜像实测（`tests/test_im_live.py`，连续 3 次通过）：
- 建群时指定的群 ID 生效；群公告随建群写入（`/group/get_groups_info` 的 `notification`）；
- 重发同一个键时 OpenIM 返回 1202，仍然只有这一个群；
- `ws_notice` 在群里，能发“自动接力已暂停”的提示。
- **更正第 13 节**：imAdmin **不入群**。CreateGroup 把调用者加进的只是核对用户、计算成员数的列表，真正入群的只有群主、管理员和成员（`joinGroupFunc` 只对这三类调用）。对功能没有影响：群里的提示由 `ws_notice` 发。

### 14.2 图片（契约第 5.4 节）

- **数据表**（迁移 0016）：`im_media`，id 同时是 `upload_id` 与 `media_id`，记录类型、大小、宽高、对象名、发到了哪个会话。
- **上传**（`POST /im/uploads`）：
  - 请求体就是图片，≤ 10 MiB（超出 413）；每人每分钟 30 次。
  - 类型与尺寸按文件头识别（`app/domain/images.py`）：只认 PNG、JPEG、GIF、WebP，SVG 等一律 422；不看 `Content-Type`。只读文件头、不解码像素，所以不引入图片库，也没有解码库的攻击面。
  - 对象名 `myws/<工作空间>/<id>.<扩展名>`，用第 3 部分核过的分片流程上传（`ObjectStore`）。
  - 没有用于发消息的上传，`IM_UPLOAD_TTL_MINUTES`（默认 60）后由清理任务删除记录。OpenIM 没有删除对象的接口，对象本身留在对象存储里，但工作台不会再给出它的地址。
- **发图**：
  - `kind=image` 加 `upload_id`：只能用自己上传、没用过、没过期的图片，其他一律 404。
  - 消息是 102，PictureElem 的原图与大图地址相同，缩略图地址带 `thumb=1`（最长边 256）；OpenIM 的校验已在真实实例上通过。
- **实例取图的地址**：
  - openclaw-channel 收到图片时直接对 `sourcePicture.url` 发不带任何凭证的 GET（插件源码 `src/inbound.ts` 的 `fetchImageAsContentPart`）。所以地址必须是实例能直接访问的绝对地址，而同一条群消息会发给内网和外网两种实例。
  - 决定：实例统一经本机回环 `http://127.0.0.1:<AGENT_TUNNEL_PORT>` 访问工作台，图片地址写成 `<前缀>/im/obj/m/<media_id>`（`AGENT_IM_OBJECT_BASE_URL` 可覆盖）。外网实例本来就经本机隧道；内网实例的接入包要加同样的本机代理，和接入网关一起做。
  - `media_id` 是随机 UUID，按能力地址处理：只认工作台发出的图片。接入网关的这个路径在下一部分实现，对比契约第 8 节“`/im/obj/*` 用 IM 令牌做 forward_auth”的偏差也在那里说明。
- **显示**（`GET /im/media/{media_id}`）：
  - 只给上传者本人，或者能看到图片所在会话的管理员，其他一律 404。
  - 由后端向 OpenIM 签出下载地址后取回，按上传时识别的类型返回，带 `X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'; sandbox`、`Cache-Control: private`。
  - 对象存储（`OPENIM_OBJECT_URL`，即 OpenIM 的 `IMENV_MINIO_EXTERNALADDRESS`）只有后端访问，不对外暴露。
  - 管理员接口暂不提供缩略图参数：加参数会改动 OC-1 的 OpenAPI 契约。缩略图只给实例侧的 `thumb=1` 用。
- **投影**：图片消息的地址符合 `/im/obj/m/<UUID>` 时给出 `media_id`；其他 http(s) 地址只给 `external_url`，前端不自动加载。
- **验证**：
  - 模拟 OpenIM 与对象存储 17 项（`tests/test_im_media_api.py`，含按内容识别的单元测试）；
  - 真实补丁镜像加 Garage：上传、发图、`/im/media` 取回的字节与上传的一致、投影带 `media_id`，与其他真实实例测试一起连续 3 次通过。

## 15. 第 4 部分（B6）：接入网关的 IM 部分与内网实例的取图代理（2026-11-01）

契约第 8 节。API 侧在 `app/domain/im_gateway.py` 与 `app/api/agent_api.py`，入口侧在 `infra/caddy/agent.Caddyfile`。

- **IM 令牌**：OpenIM 的用户令牌是用 share.secret 签名的 HS256 JWT（锁定源码 openimsdk/tools `tokenverify`、open-im-server `pkg/authverify/token.go`）。
  - 工作台在本地校验签名与有效期，不再调用 `/auth/parse_token`，这就是契约第 8 节留给 OC-3 核实的“签名方式”；
  - 令牌是否已被踢下线，仍由 OpenIM 的 REST 与消息网关自己判断。
- **放行条件**（三个入口共用）：
  - 令牌属于已启用、IM 已就绪的实例（`oc_*`，平台 5），总开关打开；
  - 请求经接入入口来，并且是实例登记的那个入口；外网入口还要同一实例的有效客户端证书；
  - 外网入口同一来源失败 10 次后封禁 15 分钟；
  - 任何不符都返回 401，不区分原因。
- **`/im/ws`**：Caddy 用 `forward_auth` 问 `/internal/gateway/im-auth`，通过后透传到 OpenIM 消息网关。
  - 认证时看查询参数里的 `token`、`sendID`（必须等于令牌的用户）和 `platformID`（必须是 5）；
  - 实测有两个坑：
    - Caddy 会按指令的默认顺序把 `rewrite` 排到 `forward_auth` 前面，认证看到的路径会变成 `/`，所以用 `route` 固定顺序；
    - `forward_auth` 默认把 WebSocket 升级的请求头一起带给认证请求，API 会按 WebSocket 连接处理并返回 403，所以要去掉这些请求头。
  - 入口不记访问日志，因为令牌在查询参数里。
- **`/im/api/{route}`**：API 检查后转发到 OpenIM REST（请求体 ≤ 64 KiB）。
  - 白名单就是 OC-0 抓到的 7 个接口，校验请求体里的 `userID` 等于令牌的用户；
  - `/user/get_users_info` 只能查同一工作空间的 IM 账号（实例、owner / admin、ws_notice、imAdmin）；
  - `/jssdk/*` 是 protobuf，只读顶层字段 1（ownerUserID），必须恰好一个且等于令牌的用户；
  - 被拒时：不在白名单返回 404，用户不符返回 403，响应体都用 OpenIM 的格式（`errCode 1002`）；
  - 转发时不带回上游的响应头，httpx 已解压，所以也不带 `Content-Encoding`。
- **`/im/obj/m/{media_id}`**：实例取工作台发出的图片（只读，`thumb=1` 给缩略图）。openclaw-channel 取图不带凭证，所以只认经接入入口来的请求和已经发出的图片。
  - **对契约第 8 节的偏差**：契约写的是 `/im/obj/*` 用 IM 令牌做 forward_auth 后透传 OpenIM 的对象地址。实际插件取图时不带令牌，OpenIM 的对象地址又指向不对外的对象存储，所以改成 API 自己按能力地址提供，其他 `/im/obj/*` 一律 404。
- **更正第 14 节的取图地址**：OC-2 核实过，外网入口开启客户端证书后 Host 必须与 SNI 一致；外网实例访问工作台用的是 `http://<外网域名>:<隧道端口>`（这个域名在 OpenClaw 容器里指向本机隧道）。所以默认前缀改为：
  - 配置了外网入口时：`http://<外网域名>:<AGENT_TUNNEL_PORT>`；
  - 只有内网入口时：内网入口地址；
  - 两种实例并存时，内网实例还要在接入包里把外网域名指向一个本机代理，内网入口也要接受这个域名，见下面的“内网实例的取图代理”。
- **入口的实测**：本机起 API，测试用的入口 Caddy 接入 OpenIM 的网络，按 `agent.Caddyfile` 原样加载。
  - WebSocket：正确令牌握手返回 101（消息网关接受连接）；sendID 不符或令牌被篡改都返回 401；
  - REST：转发拿到 OpenIM 的真实应答；`/friend/delete_friend` 返回 404；客户端自带的 `X-MyWS-Ingress` 会被入口改写；
  - `/im/obj/m` 路由到 API；`PUT`、其他路径返回 404。
- **内网实例的取图代理**：一条群消息同时发给内网和外网实例，图片地址只能有一个，所以内网实例也用外网域名取图。
  - 接入包（工作台配置了外网入口、实例是内网模式时）增加 `image-proxy` 服务：ghostunnel 客户端模式，在宿主机 `127.0.0.1:<AGENT_TUNNEL_PORT>` 接收明文，用 TLS 转发到内网入口，`--disable-authentication`（不带客户端证书，内网入口本来就不要）；
  - OpenClaw 容器用 `extra_hosts` 把外网域名解析到 127.0.0.1，与外网实例的本机隧道同一个做法；
  - 内网入口的站点同时接受 `{$AGENT_PUBLIC_HOST}`：代理连过去时 SNI 是内网域名、Host 是外网域名，内网入口不开客户端证书，Caddy 按 Host 选路由；
  - 内网实例的其他请求（MCP、`/agent-api`、`/im/ws`、`/im/api`）照旧直连内网入口，代理只用于取图；
  - 实测（ghostunnel v1.11.4 与上面的测试入口）：经本机代理，Host 是外网域名的请求路由到 API（不存在的图片返回 API 的 404），Host 是其他域名的请求不匹配任何站点（Caddy 默认的空 200，不进 API）。
- **测试**：
  - `tests/test_im_gateway.py`：6 项，包括令牌与 protobuf 的单元测试、forward_auth 的各种拒绝、外网证书与封禁、REST 白名单、实例取图；
  - `tests/test_im_live.py`：在真实补丁镜像上用真实令牌走一遍；
  - `tests/test_agents_api.py`：内网接入包带 `image-proxy`、不带 `tunnel`，目标是内网入口。

## 16. 第 4 部分（B7a）：接入部署——网络、回调、变量（2026-11-01）

OpenIM 仍是独立的 Compose 项目（方案第 6.1 节），这一批把它接进工作台。部署步骤写在 `docs/deployment.md` 第 17 节。

- **网络**：core 新建两个 internal 网络，OpenIM 以 external 方式加入。
  - `im`：api、worker，加上 OpenIM 的 openim-server 与 garage。API 经它调 OpenIM REST（`http://openim-server:10002`）、按签出的链接上传和下载图片（`http://garage:3900`），OpenIM 经它回调 API；
  - `agent-im`：只有 agent-gateway 与 openim-server，接入入口经它把 `/im/ws` 转发到消息网关（10001）；
  - **为什么接入入口不直接进 `im`**：API 只采用来源是接入入口固定地址的入口与证书请求头（OC-2）。接入入口和 api 同时在两个网络里时，Docker 的内置 DNS 可能把 `api` 解析成另一个网络的地址，来源就对不上了（只会失败、不会放大权限，但结果不稳定）。所以另建一个没有 api 的网络。代价是 `agent-im` 由 agent-gateway 创建，要先 `make agents-up` 再 `make openim-up`；OpenIM 只服务于 OpenClaw 实例，这个顺序可以接受；
  - OpenIM 内部的 MongoDB、Redis、etcd、Kafka 只在自己的 `openim` 网络里，`im` 网络里看不到工作台的 PostgreSQL / Redis（实测解析不到）。
- **回调**：
  - openim-server 在 `im` 网络里固定为 `<IM_NET>.2`（默认 `172.31.252.2`，子网 `/28`，动态地址只用 `.8/29`），API 的 `OPENIM_CALLBACK_ALLOWED_CIDRS` 默认就是 `<IM_NET>.2/32`；
  - 回调地址默认 `http://api:8000/internal/openim/${OPENIM_CALLBACK_PATH_TOKEN}`，口令没有设置时 OpenIM 拒绝启动。嵌套的 `:?` 何时求值随 Compose 版本而变：本机 v5.6.1 只在没有覆盖 `OPENIM_WEBHOOK_URL` 时求值，GitHub 运行器上的版本覆盖了也会检查（`ci-openim` 第一次运行因此失败），所以回归测试生成的 `.env` 也给一个随机口令；
  - **before 回调失败即拒**：锁定镜像里的 `config/webhooks.yml` 中，`beforeSendSingleMsg`、`beforeSendGroupMsg` 默认 `failedContinue: true`（回调失败照样发出），所以用 `IMENV_WEBHOOKS_BEFORESENDSINGLEMSG_FAILEDCONTINUE=false` 与 `IMENV_WEBHOOKS_BEFORESENDGROUPMSG_FAILEDCONTINUE=false` 关掉。这就是契约第 10 节要求的“OC-3 配置并实测”。
- **对象存储地址**：`IMENV_MINIO_EXTERNALADDRESS` 默认改为 `http://garage:3900`，与 API 的 `OPENIM_OBJECT_URL` 相同。只有 API 用这个地址，浏览器和实例都拿不到。
- **端口**：openim-server 不再映射到 `127.0.0.1:10001/10002`。回归测试用的 `compose.probe.yaml` 把端口加回来，并把 `im` / `agent-im` 改为自己创建（带 `-probe` 后缀），所以 `ci-openim` 不需要启动工作台。
- **变量**：`.env.example` 增加 `OPENIM_*` 与 `IM_NET`，`make env` 按格式生成（Garage 的 RPC 密钥与 S3 密钥是 64 位十六进制，访问密钥 ID 是 `GK` 加 24 位十六进制）。`OPENIM_API_URL`、`OPENIM_OBJECT_URL` 默认留空：部署了 OpenIM 再填，没部署时 IM 接口返回 503。
- **恢复演练**：api 现在加入有固定网段的 `im` 网络，`make restore-verify` 的临时环境改用 `IM_NET=172.31.253`，并且不连接 OpenIM，否则网段冲突、临时环境起不来。
- **Makefile**：`openim-build`、`openim-up`（先检查 core 的两个网络是否存在）、`openim-down`、`openim-logs`。
- **实测中发现并修正**：API 的访问日志记录实际路径，回调口令原样出现在日志里。访问日志现在把这类凭证段换成 `***`：回调口令，以及实例取图的能力地址 `/im/obj/m/<media_id>`。
- **端到端实测**（本会话的 Docker；按 `make up` → `make agents-up` → `make openim-up` 的顺序，用新的 Compose 项目与全新数据卷；api 镜像沿用本地已构建的版本，挂载当前源码，依赖没有变化）：
  - openim-server 拿到 `172.31.252.2`，api 经 `im` 网络调通 OpenIM REST；
  - 管理员打开 IM 页面后注册了 IM 账号；登记的两个实例 `im_ready`；
  - 管理员发文字和图片：before 回调到达 API 并放行（失败即拒的配置下能发出，就说明回调走通了），after 回调进收件箱、Worker 投影完成；
  - imAdmin 代实例甲给实例乙发单聊：API 的 before 回调拒收，OpenIM 返回 5003；
  - 停掉 api 后管理员发消息：OpenIM 返回 90004，消息没有发出；api 恢复后正常发出；
  - 经真实的 agent-gateway：`/im/ws` 用插件实际的查询参数握手返回 101，sendID 冒充 401；`/im/api/friend/get_full_friend_user_ids` 拿到真实好友列表；读 owner 的会话 403；`/im/obj/m/<media_id>` 取回的字节与上传一致；
  - 实例连上又断开后，上线、下线回调进收件箱，实例状态变为 offline；
  - `im` 网络里另一个地址（`172.31.252.12`）带着正确口令伪造回调：404；
  - 访问日志里的回调路径是 `/internal/openim/***/callback…Command`。
- **还没做**：备份覆盖 OpenIM 的 MongoDB 与 Garage（B7b）；外网模式的 IM 连接经本机隧道与双向证书的端到端验证。

## 17. 第 4 部分（B7b）：备份与恢复覆盖 OpenIM（2026-11-01）

方案第 6.1 节：restic 增加 OpenIM 的 MongoDB 导出与对象存储；恢复演练覆盖 OpenIM，恢复后消息历史和好友关系一致。实现在 `scripts/openim_backup.sh`，`backup.sh`、`restore-verify.sh` 引入它。

- **备份什么**：
  - MongoDB：`mongodump --db openim_v3 --archive --gzip`。用 OpenIM 自己的用户（只有这个库的读写权限）；密码取 mongo 容器自己的环境变量，写进容器里权限 600 的临时配置文件，不出现在命令行；统计用 mongosh 在容器里读同一个环境变量；
  - Garage：`garage meta snapshot`（v2.4.1 的命令，快照是 `meta/snapshots/<时间>/db.sqlite`，实测只保留最近两份），再从只读挂载的数据卷打包：元数据目录里除正在使用的 `db.sqlite*` 与快照目录外原样打包（节点密钥、集群布局等，恢复后节点 ID 不变，布局才对得上），数据库换成刚做的快照，数据块目录整个打包。用符号链接加 `tar -h` 一次输出，不在容器里复制数据；
  - 小对象（实测 158 字节的图片）内联在元数据里，大对象（270 KB）才有数据块，两种都验证过；
  - `openim-info.json` 记下各集合的文档数（user、friend、group、group_member、conversation、msg、seq、seq_user）与桶里的对象数；
  - 不备份 Kafka、Redis、etcd（理由见下面“序号”一条）。
- **何时备份**：按 Compose 标签找 OpenIM 的 mongo 与 garage 容器（项目名默认 `<COMPOSE_PROJECT_NAME>-openim`，可用 `OPENIM_PROJECT` 指定），都在运行才备份；配置了 `OPENIM_API_URL` 却没在运行时，照常备份工作台并警告。
- **恢复检查**（`make restore-verify` 与 `ci-openim`）：在 `--network none` 的临时容器里，用部署时锁定的同一个镜像，分别 `mongorestore` 与解开 Garage 归档后启动单节点 Garage（RPC 密钥临时生成，节点身份来自归档），核对文档数与对象数。临时容器与数据卷在函数返回时删除。
- **真实恢复**：`scripts/openim_backup.sh restore <目录> [项目名]`。只写入全新的数据卷：项目还有容器或数据卷时拒绝。步骤是 `compose create garage mongo` → 解开 Garage 归档 → 启动 mongo（第一次启动建 OpenIM 用户）→ `mongorestore --drop` → `make openim-up`（garage-init 幂等，确认布局、密钥与桶）。
- **序号会往前跳**（实测）：OpenIM 在 MongoDB 里按块预留会话序号，当前值只缓存在 Redis。只恢复 MongoDB 时，`newest_seq` 返回预留块的末尾（实测从 5 变成 51），新消息从 52 开始；中间的空号由 `pull_msg_by_seq` 返回 `status: 4`、没有 serverMsgID 与发送人的占位项，工作台的投影解析时要求这两项，会跳过它们（`im_projection.py`）。
  - 不备份 Redis 是有意的：Redis 与 MongoDB 不是同一时刻的快照，恢复旧的 Redis 可能让序号回退，新消息就会和已有消息撞号。往前跳只多出空号，不丢消息、不覆盖。
- **实测**（本会话的 Docker）：
  - `scripts/backup.sh`：工作台归档 282 项加 OpenIM 两份归档写进同一个 restic 快照；`scripts/restore-verify.sh`：冒烟与数据检查通过，OpenIM 隔离恢复检查数量一致；
  - `scripts/openim_backup.sh check`：B7a 的端到端环境与越权测试用的环境（189 个用户、307 个会话、137 个消息文档、18 个对象，含对象检查留下的 12 MB 分片对象）都通过；
  - 真实恢复演练：停掉原来的 OpenIM，把归档恢复到全新的项目并启动。好友关系一致；两个会话的真实消息逐条一致（只多出上面说的空号）；270 KB 的图片经接入入口 `/im/obj/m` 取回，字节一致；恢复后管理员发的两条消息序号是 52、53，投影正常。
- **CI**：`ci-openim` 在对象检查之后运行 `scripts/openim_backup.sh check`，触发路径加上这个脚本。A15（`ci-infra`）的环境没有 OpenIM，快照里没有 OpenIM 时 `restore-verify` 只提示一句。
- **外网模式 IM 端到端**（补上第 15、16 节留下的一项）：在同一套环境里登记外网实例、下载接入包，用包里的客户端证书起 ghostunnel 本机隧道（与接入包的 `tunnel` 服务同样的参数），全部请求经隧道到外网入口（双向证书）：
  - 用实例凭证经 `/agent-api/v1/im-token` 领到 IM 令牌；
  - `/im/ws` 用插件实际的查询参数握手 101；`/im/api/friend/get_full_friend_user_ids` 返回管理员；伪造的 IM 令牌 401；
  - 管理员给外网实例发图，按图片消息里的地址 `http://<外网域名>:18443/im/obj/m/<media_id>` 经隧道取回，字节一致；`thumb=1` 返回缩略图。
