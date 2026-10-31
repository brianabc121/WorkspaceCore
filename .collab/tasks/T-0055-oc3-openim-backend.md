---
id: T-0055
title: OC-3 OpenIM 部署与 IM 后端：infra/openim、连接器、好友同步、回调收件箱、消息投影与对账、群管理、防刷屏、图片（S6-02）
owner: claude
status: todo
priority: P1
branch:
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

- [ ] 自建打过补丁的 OpenIM 镜像（OC-0 ADR 第 6 节），锁定 digest；`infra/openim/probes/rest-authz.py` 对补丁镜像退出码为 0，并进入 CI
- [ ] 评估并替换 bitnamilegacy 的 kafka / minio / etcd，开启 etcd 认证与 Kafka SASL，调小 Kafka 日志段与保留期（从 OC-0 移来）
- [ ] 图片：对象存储上传流程核验与实现（从 OC-0 移来）；OC-0 ADR F1（签发令牌会踢下线）、F2（导入好友前先查关系）、F7（日志里的消息内容）落实

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
