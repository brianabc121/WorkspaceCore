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
