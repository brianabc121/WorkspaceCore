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
updated: 2026-10-31
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
- [ ] 评估并替换 bitnamilegacy 的 kafka / minio / etcd，开启 etcd 认证与 Kafka SASL，调小 Kafka 日志段与保留期（从 OC-0 移来）
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
