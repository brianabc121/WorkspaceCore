# OpenIM（管理员端 IM 的后端）——OC-3 进行中

> **部署步骤见 `docs/deployment.md` 第 17 节**（`make openim-build` → `make agents-up` → `make openim-up`）。
> 已接入工作台的内部网络、回调地址与接入入口；`make backup` 会一并备份 OpenIM 的 MongoDB 与 Garage（第 17.5 节）。
> **不要换回官方镜像** `openim/openim-server:v3.8.3-patch.18`：任何一个 IM 用户的令牌都能读到别人的私聊，
> 还能改别人的会话设置（OC-0 ADR 第 4 节）。

- 设计依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.4、4.5、6.1 节
- 核验结论：`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`
- 补丁镜像：`docs/decisions/2026-10-31-v2-oc3-openim-patched-image.md`（人类 2026-10-31 同意自建，不向上游报告）

## 文件

| 文件 | 用途 |
|---|---|
| `compose.yaml` | 独立的 Compose 项目：mongo、redis、etcd、kafka、garage（对象存储）、openim-server。<br/>第三方镜像全部锁定 tag 加 digest；openim-server 用本机构建的补丁镜像（`pull_policy: never`）；<br/>密码全部来自仓库根目录的 `.env`（`make env` 生成），`:?` 表示必填；不发布任何端口；<br/>openim-server 加入 core 的 `im`（固定 `<IM_NET>.2`）与 `agent-im` 网络，garage 加入 `im` |
| `compose.probe.yaml` | 只用于越权回归测试的覆盖文件：加一个放行一切的回调桩，`im` / `agent-im` 改为自己创建（不需要工作台），REST 与消息网关绑定本机。不要在生产环境使用 |
| `mongo-init.sh` | MongoDB 第一次初始化时创建 OpenIM 专用用户，只有 `openim_v3` 库的读写权限 |
| `etcd-init.sh` | 一次性服务 `etcd-init` 执行：开启 etcd 认证，`openim` 用户只能读写 `openim` 前缀与启动检查用的键；以后每次启动同步授权与密码 |
| `garage.toml`、`garage-init.sh` | Garage 配置（单节点）与一次性服务 `garage-init`：应用布局、导入访问密钥、建桶 `openim` 并授权（幂等） |
| `patches/*.patch` | 对上游 v3.8.3-patch.18 的补丁（`git am` 顺序应用） |
| `server/build.sh`、`server/Dockerfile` | 构建补丁镜像：校验上游提交 SHA → 打补丁 → 构建（构建输入全部锁定） |
| `probes/rest-authz.py` | 越权回归测试，覆盖 REST 和 WebSocket。退出码：0 = 全部挡住；1 = 有越权；2 = 无法判断 |
| `probes/ws-pull-other-user.mjs` | WebSocket 部分（`rest-authz.py` 会自动调用）。Node 22 及以上，用内置 WebSocket |
| `probes/webhook-stub.py` | `compose.probe.yaml` 用的回调桩（放行一切，不记录载荷） |
| `probes/object-check.py` | 对象存储检查：小文件、12 MB 分片、图片缩略图，经 OpenIM 上传、下载、比对。要在能访问预签名链接的地方运行 |

## 构建与本机核验（不接工作台）

部署到工作台见 `docs/deployment.md` 第 17 节；下面是不启动工作台、只核验 OpenIM 本身的做法（CI 的 `ci-openim` 也是这样）。

```
infra/openim/server/build.sh        # 生成 my-workspace/openim-server:v3.8.3-patch.18-myws.2
# 生成 infra/openim/.env（不提交）：每项都用 openssl rand -hex 16 生成，禁止使用 openIM123：
#   OPENIM_SECRET OPENIM_MONGO_ROOT_PASSWORD OPENIM_MONGO_PASSWORD OPENIM_REDIS_PASSWORD
#   OPENIM_ETCD_ROOT_PASSWORD OPENIM_ETCD_PASSWORD OPENIM_KAFKA_PASSWORD
#   OPENIM_GARAGE_RPC_SECRET 与 OPENIM_S3_SECRET_KEY 用 openssl rand -hex 32；OPENIM_GARAGE_ADMIN_TOKEN 随机；
#   OPENIM_S3_ACCESS_KEY=GK$(openssl rand -hex 12)
# 回归测试时 OPENIM_WEBHOOK_URL=http://webhook-stub:10006；OPENIM_CALLBACK_PATH_TOKEN 也要给一个随机值
# （有的 Compose 版本即使覆盖了回调地址也会检查这个必填变量）
docker compose -p my-workspace-openim -f infra/openim/compose.yaml -f infra/openim/compose.probe.yaml \
  --env-file infra/openim/.env up -d
python3 -I infra/openim/probes/rest-authz.py --api http://127.0.0.1:10002 --secret "$OPENIM_SECRET"
# 对象存储检查要能访问预签名链接：OPENIM_OBJECT_EXTERNAL_URL=http://garage:3900，并在 openim 网络里运行
docker run --rm --network my-workspace-openim_openim -v "$PWD/infra/openim/probes/object-check.py:/c.py:ro" \
  python:3.12.15-slim-trixie python -I /c.py --api http://openim-server:10002 --secret "$OPENIM_SECRET"
```

- Docker Hub 限流时：`BASE_REGISTRY=mirror.gcr.io/library infra/openim/server/build.sh`（digest 不变）。
- 构建要 8 GB 左右的空闲磁盘（Go 编译缓存）。
- CI：`ci-openim` 工作流在 `infra/openim/**` 变化时执行上面几步，两个检查都必须以 0 结束；另外用 trivy 扫描镜像（高危只报告，有修复的严重漏洞就失败）。
- 从 bitnamilegacy 换成官方 etcd / Kafka 和 Garage 后，数据卷名变了（`openim-etcd-data`、`openim-kafka-data`、`openim-garage`）。旧卷（含 `openim-minio`）确认不再需要后可以手动删除；MinIO 里的对象不会自动迁移到 Garage。

| 检查 | 官方镜像（OC-0） | 补丁镜像（OC-3） |
|---|---|---|
| `search_msg` 读取 admin↔b | ❌ 越权成功 | ✅ 已挡住 |
| `pull_msg_by_seq` 冒充 admin | ❌ 越权成功 | ✅ 已挡住 |
| `get_conversations` 读取 admin 的会话 | ❌ 越权成功 | ✅ 已挡住 |
| `set_conversations` 修改 admin 的会话 | ❌ 越权成功 | ✅ 已挡住 |
| WebSocket 1002 冒充 admin 拉取 admin↔b | ❌ 越权成功 | ✅ 已挡住 |
| 退出码 | 1 | **0** |

## OC-3 还要完成的事

1. ~~对象存储接入~~：预签名链接只给工作台 API 用（`http://garage:3900`），实例取图走接入入口的 `/im/obj/m/<media_id>`（OC-3 ADR 第 14、15 节）。
2. ~~接入工作台~~：回调地址指向 API 的内部路径，before 回调失败即拒；不发布端口，只经接入入口访问；变量进了 `.env.example` 与 `make env`（OC-3 ADR 第 16 节）。
3. ~~备份与恢复~~：MongoDB 归档与 Garage 快照进 restic 快照，`make restore-verify` 与 `ci-openim` 做隔离恢复检查，`scripts/openim_backup.sh restore` 做真实恢复（OC-3 ADR 第 17 节）。
4. **资源**：空闲时（2026-11-01）openim-server 约 392 MiB、Kafka 约 367 MiB、MongoDB 约 187 MiB、Garage 约 9 MiB、etcd 约 16 MiB；Kafka 数据卷空闲时不到 1 MB。压测后补充峰值数据。
