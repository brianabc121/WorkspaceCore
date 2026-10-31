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
- 替换 bitnamilegacy 的 minio：和图片上传流程一起核验（官方 MinIO 镜像的发布状态要先核实，见第 7.4 节）；
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
