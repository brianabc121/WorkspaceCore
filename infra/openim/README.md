# OpenIM（管理员端 IM 的后端）——OC-3 进行中

> **还不能用于生产**：补丁镜像已经就绪（下文），但 OpenIM 还没接入工作台（内部网络、回调地址、接入入口、备份）。
> **不要换回官方镜像** `openim/openim-server:v3.8.3-patch.18`：任何一个 IM 用户的令牌都能读到别人的私聊，
> 还能改别人的会话设置（OC-0 ADR 第 4 节）。

- 设计依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.4、4.5、6.1 节
- 核验结论：`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`
- 补丁镜像：`docs/decisions/2026-10-31-v2-oc3-openim-patched-image.md`（人类 2026-10-31 同意自建，不向上游报告）

## 文件

| 文件 | 用途 |
|---|---|
| `compose.yaml` | 独立的 Compose 项目：mongo、redis、etcd、kafka、minio、openim-server。<br/>第三方镜像全部锁定 tag 加 digest；openim-server 用本机构建的补丁镜像（`pull_policy: never`）；<br/>密码全部来自环境变量，`:?` 表示必填，没有默认值 |
| `compose.probe.yaml` | 只用于越权回归测试的覆盖文件：加一个放行一切的回调桩。不要在生产环境使用 |
| `mongo-init.sh` | MongoDB 第一次初始化时创建 OpenIM 专用用户，只有 `openim_v3` 库的读写权限 |
| `etcd-init.sh` | 一次性服务 `etcd-init` 执行：开启 etcd 认证，`openim` 用户只能读写 `openim` 前缀与启动检查用的键；以后每次启动同步授权与密码 |
| `patches/*.patch` | 对上游 v3.8.3-patch.18 的补丁（`git am` 顺序应用） |
| `server/build.sh`、`server/Dockerfile` | 构建补丁镜像：校验上游提交 SHA → 打补丁 → 构建（构建输入全部锁定） |
| `probes/rest-authz.py` | 越权回归测试，覆盖 REST 和 WebSocket。退出码：0 = 全部挡住；1 = 有越权；2 = 无法判断 |
| `probes/ws-pull-other-user.mjs` | WebSocket 部分（`rest-authz.py` 会自动调用）。Node 22 及以上，用内置 WebSocket |
| `probes/webhook-stub.py` | `compose.probe.yaml` 用的回调桩（放行一切，不记录载荷） |

## 构建与本机核验

```
infra/openim/server/build.sh        # 生成 my-workspace/openim-server:v3.8.3-patch.18-myws.2
# 生成 infra/openim/.env（不提交）：每项都用 openssl rand -hex 16 生成，禁止使用 openIM123：
#   OPENIM_SECRET OPENIM_MONGO_ROOT_PASSWORD OPENIM_MONGO_PASSWORD OPENIM_REDIS_PASSWORD OPENIM_MINIO_SECRET
#   OPENIM_ETCD_ROOT_PASSWORD OPENIM_ETCD_PASSWORD OPENIM_KAFKA_PASSWORD
# 回归测试时 OPENIM_WEBHOOK_URL=http://webhook-stub:10006
docker compose -p my-workspace-openim -f infra/openim/compose.yaml -f infra/openim/compose.probe.yaml \
  --env-file infra/openim/.env up -d
python3 -I infra/openim/probes/rest-authz.py --api http://127.0.0.1:10002 --secret "$OPENIM_SECRET"
```

- Docker Hub 限流时：`BASE_REGISTRY=mirror.gcr.io/library infra/openim/server/build.sh`（digest 不变）。
- 构建要 8 GB 左右的空闲磁盘（Go 编译缓存）。
- CI：`ci-openim` 工作流在 `infra/openim/**` 变化时执行上面三步，测试脚本必须以 0 结束；另外用 trivy 扫描镜像（高危只报告，有修复的严重漏洞就失败）。
- 从 bitnamilegacy 的 etcd / Kafka 换成官方镜像后，数据卷名变了（`openim-etcd-data`、`openim-kafka-data`）。旧卷可以在确认不再需要后手动删除。

| 检查 | 官方镜像（OC-0） | 补丁镜像（OC-3） |
|---|---|---|
| `search_msg` 读取 admin↔b | ❌ 越权成功 | ✅ 已挡住 |
| `pull_msg_by_seq` 冒充 admin | ❌ 越权成功 | ✅ 已挡住 |
| `get_conversations` 读取 admin 的会话 | ❌ 越权成功 | ✅ 已挡住 |
| `set_conversations` 修改 admin 的会话 | ❌ 越权成功 | ✅ 已挡住 |
| WebSocket 1002 冒充 admin 拉取 admin↔b | ❌ 越权成功 | ✅ 已挡住 |
| 退出码 | 1 | **0** |

## OC-3 还要完成的事

1. **替换 MinIO**（etcd、Kafka 已换成官方镜像并开启认证）：和图片上传流程一起核验，见 OC-3 ADR 第 7.4 节。
2. **接入工作台**：
   - 回调地址指向工作台 API 的内部路径（回调没有签名，只能走内部网络）；
   - 去掉 `127.0.0.1` 端口映射，改为只经 Caddy 接入入口访问；
   - 变量加进 `.env.example` 和 `make env`。
3. **备份与恢复**：MongoDB 导出和 MinIO 数据纳入 restic 备份，A15 恢复演练覆盖 OpenIM。
4. **资源**：空闲时（myws.2 + 官方 etcd / Kafka，2026-10-31）openim-server 约 395 MiB、Kafka 约 370 MiB、MongoDB 约 354 MiB、MinIO 约 125 MiB、etcd 约 18 MiB；Kafka 数据卷空闲时不到 1 MB。压测后补充峰值数据。
