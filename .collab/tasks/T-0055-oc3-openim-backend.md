---
id: T-0055
title: OC-3 OpenIM 部署与 IM 后端：infra/openim、连接器、好友同步、回调收件箱、消息投影与对账、群管理、防刷屏、图片（S6-02）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app,services/api/migrations,services/api/tests,infra/openim,infra/backup,scripts
depends_on: T-0054
created: 2026-10-31
updated: 2026-11-01
---

# T-0055 OC-3 OpenIM 部署与 IM 后端：infra/openim、连接器、好友同步、回调收件箱、消息投影与对账、群管理、防刷屏、图片（S6-02）

## 目标
部署 OpenIM，实现 IM 后端（方案第 4.4、4.5、6.1 节）。

## 验收标准
- [ ] `infra/openim/compose.yaml`：digest 锁定、随机密码、不映射数据库端口；备份覆盖 MongoDB 与 MinIO
- [ ] OpenIM 连接器：管理员令牌、用户注册、好友导入、建群与成员管理、代发、IM 令牌签发
- [ ] 回调收件箱去重、消息投影、30 秒增量对账与每日全量对账；`/im/api` 接入网关的白名单与用户 ID 校验
- [ ] 会话置顶、单条消息置顶、免打扰、分组、不能删好友（拦截加自动补回）、上线自动加好友
- [ ] 防刷屏（连续 8 条、每分钟 6 条、可按群调整），禁止实例之间单聊；图片上传与代理显示，外链图片不加载
- [ ] 后端测试覆盖以上规则，以及回调丢失后的对账

- [x] 自建打过补丁的 OpenIM 镜像（OC-0 ADR 第 6 节），锁定 digest；`infra/openim/probes/rest-authz.py` 对补丁镜像退出码为 0，并进入 CI
- [x] 评估并替换 bitnamilegacy 的 kafka / minio / etcd，开启 etcd 认证与 Kafka SASL，调小 Kafka 日志段与保留期（从 OC-0 移来）
- [ ] 图片：对象存储上传流程核验与实现（从 OC-0 移来）；OC-0 ADR F1（签发令牌会踢下线）、F2（导入好友前先查关系）、F7（日志里的消息内容）落实
- [ ] 接入入口放行 `/im/ws`、`/im/api/*`、`/im/obj/*`（forward_auth 与网关），IM 连接经外网模式的本机隧道与双向证书跑通（从 OC-2 移来）

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 21:04 UTC · claude · 开始任务
- 2026-10-31 21:06 UTC · claude · 第 1 部分：补丁镜像。infra/openim/patches/ 三个补丁：消息网关内层请求绑定连接用户、1003 发送人必须是连接用户且不能发通知类消息（OC-0 未发现，附单元测试）、1004 拒绝；消息 RPC 读取与已读接口校验调用者、搜索只给管理员；会话 REST 层校验属主（会话 RPC 有代表别的用户的内部调用，不能在 RPC 里校验，偏离 OC-0 ADR 第 6 节原计划）。infra/openim/server/build.sh 校验上游提交 SHA 后打补丁构建，基础镜像与 mage 锁定；“锁定 digest”落实为锁定全部构建输入（自建镜像每次构建 digest 不同）。本机：rest-authz.py 退出码 0，正常使用不受影响。新 CI 工作流 ci-openim。trivy：严重 0、高危来自上游依赖，升级放到下一部分。越出 paths：.github/workflows/ci-openim.yml、docs/decisions、docs/versions.md。下一步：替换 bitnamilegacy、升级依赖、接入工作台
- 2026-10-31 22:03 UTC · claude · 第 2 部分：补丁 0004 升级有高危漏洞的 Go 依赖（x/net 0.60.0、x/image、compress、grpc 1.83.2、go-jose 4.1.6 等，go 指令 1.26.0），镜像改名 myws.2，trivy 只剩 Go 标准库 1.26.9（修复版不满两周）；ci-openim 加镜像扫描（严重漏洞失败）。etcd 换官方 gcr.io/etcd-development/etcd v3.5.34 并由 etcd-init（alpine + JSON 网关）开启认证，openim 用户只能读写 openim 前缀与 /check_openim_component；Kafka 换 apache/kafka 3.9.2（不用 4.x：OpenIM 消费者按 2.0 协议连接）并开启 SASL/PLAIN，日志段 16 MiB / 保留 72 小时 / 索引 1 MiB，空闲数据卷从 1.74 GB 降到 648 KB。本机：rest-authz.py 退出码 0，正常使用、经 Kafka 中转的消息、未认证访问被拒均已验证。MinIO 还没换（官方镜像发布状态无法核实），和图片上传一起做。下一步：对象存储与图片、接入工作台
- 2026-11-01 02:07 UTC · claude · 第 3 部分：对象存储换成 Garage v2.4.1（MinIO 官方镜像已不在 Docker Hub、bitnami 没了）。garage.toml 单节点（监听 0.0.0.0，本机 Docker 无 IPv6），garage-init（alpine + 管理 API v2）幂等：应用布局、导入密钥（不一致报错，Garage 不允许复用已删除的密钥 ID）、建桶授权。OpenIM 继续用 minio 驱动。实测：MinIO 对照与 Garage 都通过预签名单次上传与 12 MB 分片上传（ETag=MD5、下载一致），Garage 上缩略图 400×300→64×48，匿名访问 403，rest-authz.py 0；Garage 空闲 9 MiB（MinIO 125 MiB）。新探针 probes/object-check.py 进入 ci-openim。bitnamilegacy 已全部替换，勾选对应验收项。下一步：接入工作台（内部网络、回调、/im/* 入口、.env.example、备份）与 IM 后端
- 2026-11-01 02:42 UTC · claude · 第 4 部分 B1：OpenIM 连接器 app/integrations/openim.py（imAdmin 令牌缓存在 Redis、令牌错误重取一次、errCode 归类、用户 / 好友 / 令牌 / 强制下线 / 代发 / 按序号拉取 / 群 / 会话设置）与配置项 OPENIM_API_URL、OPENIM_SECRET、OPENIM_CALLBACK_PATH_TOKEN、OPENIM_CALLBACK_ALLOWED_CIDRS。真实实例测试 tests/test_openim_live.py 连续 3 次通过；核实：ws_/oc_+32 位十六进制可用；IM 令牌有效期不能按次指定（改 tokenPolicy.expire）；protobuf 包装类型字段按普通值写（写 {value} 返回 1001）；重复注册 1102。数据表放到下一批（回调收件箱、账号登记、im-token）
- 2026-11-01 03:08 UTC · claude · 第 4 部分 B2（本地完成，待 PR）：迁移 0012（im_accounts / im_groups / im_group_members / im_webhook_inbox）；app/domain/im.py 账号登记（登记实例后注册 oc_、与 owner/admin 互加好友，成功才 im_ready；失败由 Worker 每 5 分钟 provision_im 重试）、停用与总开关强制下线；/agent-api/v1/im-token（每小时 10 次、no-store、503 IM_UNAVAILABLE）；回调 /internal/openim/<口令>/<命令>（404 规则、before 回调 5001–5006/5009、after 回调入收件箱去重、其他命令计数）。测试：模拟 OpenIM + 真实库 12 项，全量 pytest 463 passed（契约测试 1 项从 501 改为 404）。下一步 B3：收件箱处理与消息投影、对账；ws_notice 群提示随群接口做
- 2026-11-01 03:23 UTC · claude · 第 4 部分 B3（本地完成，等 PR #85 合并后提交）：迁移 0013（im_conversations / im_messages）；app/domain/im_projection.py：回调只当提示，newest_seq + pull_msg_by_seq 按序号投影（每会话最多补最近 1000 条），消息类型按锁定 protocol 常量解析；收件箱处理（SKIP LOCKED、重试 8 次进 dead_letter）：上线改 online 并补好友，下线先核对 OpenIM 实时在线状态，乱序丢弃，停用实例再踢，删好友自动补回并通知；对账每 30 秒增量、每天全量；消息保留期 IM_MESSAGE_RETENTION_DAYS=365。模拟 OpenIM 17 项通过；真实补丁镜像端到端 tests/test_im_live.py 连续 3 次通过。
- 2026-11-01 03:47 UTC · claude · 第 4 部分 B4a（本地完成，等 PR #86 合并后提交）：管理员 IM 接口——状态（首次打开注册账号并补好友）、角标、会话列表 / 打开 / 置顶免打扰（先同步 OpenIM，失败 503 不改本地）/ 已读、消息分页、幂等发送（Idempotency-Key 写进 ex；拒收 409、没发出 503 可重试、结果未知 504 后按 ex 找回或 5 分钟后 409 未生效；每人每分钟 30 条）、消息置顶（上限 10）、导出 NDJSON（审计）。迁移 0014（im_message_pins、im_messages.idempotency_key）。模拟 OpenIM 9 项；真实补丁镜像 3 项连续 3 次通过。下一批 B4b：通讯录与分组、群接口（拉入 ws_notice、暂停提示）
- 2026-11-01 04:09 UTC · claude · 第 4 部分 B4b（本地完成，等 PR #87 合并后提交）：群接口（建群幂等：群 ID 由 Idempotency-Key 推出，OpenIM 1202 视为已建；ws_notice 进群；改名公告 / 邀请 / 移出 / 解散先调 OpenIM，失败 503；防刷屏设置乐观并发，paused 取 Redis）、自动接力暂停时 ws_notice 群内提示、通讯录（含停用实例）、分组（名称唯一 409 CONTACT_GROUP_EXISTS，已补进契约第 12 节）、会话按分组筛选。迁移 0015。模拟 OpenIM 5 项通过。Docker 守护进程已到 2 小时时限停止（未重启），本地测试改用原生 PostgreSQL 16（54330）与 Redis（63800）；指定群 ID 与随建群写公告尚未在真实实例上验证，接入部署时补。
