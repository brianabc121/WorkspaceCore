# OpenIM（管理员端 IM 的后端）——OC-0 初稿

> **不得用于生产**：官方 `openim/openim-server:v3.8.3-patch.18` 有越权问题。
> 任何一个 IM 用户的令牌都能读到别人的私聊，还能改别人的会话设置（ADR 第 4 节）。
> OC-3 会换成按本目录 `patches/` 自建的镜像（人类 2026-10-31 同意，不向上游报告）。

- 设计依据：`docs/v2/TRANSFORMATION_PLAN.md` 第 4.4、4.5、6.1 节
- 核验结论：`docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`

## 文件

| 文件 | 用途 |
|---|---|
| `compose.yaml` | 独立的 Compose 项目：mongo、redis、etcd、kafka、minio、openim-server。<br/>镜像全部锁定 tag 加 digest；密码全部来自环境变量，`:?` 表示必填，没有默认值 |
| `mongo-init.sh` | MongoDB 第一次初始化时创建 OpenIM 专用用户，只有 `openim_v3` 库的读写权限 |
| `probes/rest-authz.py` | 越权回归测试，覆盖 REST 和 WebSocket。退出码：0 = 全部挡住；1 = 有越权；2 = 无法判断 |
| `probes/ws-pull-other-user.mjs` | WebSocket 部分（`rest-authz.py` 会自动调用）。Node 22 及以上，用内置 WebSocket |

## 本机核验

```
# 生成 infra/openim/.env（不提交）：每项都用 openssl rand -hex 16 生成，禁止使用 openIM123
docker compose -f infra/openim/compose.yaml --env-file infra/openim/.env up -d
python3 -I infra/openim/probes/rest-authz.py --api http://127.0.0.1:10002 --secret "$OPENIM_SECRET"
```

OC-0 用官方镜像实测的结果（2026-10-31）：

| 检查 | 结果 |
|---|---|
| `search_msg` 读取 admin↔b | ❌ 越权成功 |
| `pull_msg_by_seq` 冒充 admin | ❌ 越权成功 |
| `get_conversations` 读取 admin 的会话 | ❌ 越权成功 |
| `set_conversations` 修改 admin 的会话 | ❌ 越权成功 |
| WebSocket 1002 冒充 admin 拉取 admin↔b | ❌ 越权成功 |

脚本以退出码 1 结束。**打过补丁的镜像必须让它以 0 结束**，这一步在 OC-3 的 CI 里执行。

## OC-3 要完成的事

1. **补丁与自建镜像**（ADR 第 6 节）：
   - 消息网关：用连接的用户覆盖内层请求的用户字段；
   - RPC：消息、会话接口校验调用者；
   - 从 v3.8.3-patch.18 源码构建，锁定 digest。
2. **替换不再维护的镜像**：评估把 bitnamilegacy 的 kafka、minio、etcd 换成各组件官方镜像；开启 etcd 认证和 Kafka SASL（openim-docker 已经支持）。
3. **接入工作台**：
   - 回调地址指向工作台 API 的内部路径（回调没有签名，只能走内部网络）；
   - 去掉 `127.0.0.1` 端口映射，改为只经 Caddy 接入入口访问；
   - 变量加进 `.env.example` 和 `make env`。
4. **备份与恢复**：MongoDB 导出和 MinIO 数据纳入 restic 备份，A15 恢复演练覆盖 OpenIM。
5. **资源**：空闲时 OpenIM 合计约 1.4 GiB 内存，其中 Kafka 约 720 MiB（ADR 第 8 节）；压测后补充峰值数据。
