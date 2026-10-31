---
id: T-0052
title: OC-0 核验与选型：OpenClaw extended-stable、openclaw-channel 兼容性、OpenIM 部署与接口、exec 密钥、外网隧道、假模型（S6-01）
owner: claude
status: todo
priority: P1
branch:
paths: docs/decisions,docs/versions.md,infra/openclaw,infra/openim
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0052 OC-0 核验与选型：OpenClaw extended-stable、openclaw-channel 兼容性、OpenIM 部署与接口、exec 密钥、外网隧道、假模型（S6-01）

## 目标
在锁定版本的真实实例上核验改造方案依赖的上游能力（方案第 9、10 节），把不确定项变成有来源的结论；核验不了的写成接口桩和 TODO。

## 验收标准
- [ ] 锁定 OpenClaw extended-stable 版本与镜像 digest；`openclaw-channel` 在该版本上能收发私聊和群聊，结论写进 ADR
- [ ] OpenIM v3.8.3-patch.18 按 `infra/openim/` 初稿启动，全部镜像锁 digest，评估 bitnamilegacy 的替代镜像，实测内存与磁盘
- [ ] 抓包列出插件用到的 OpenIM REST 接口，作为接入网关的白名单；核验 WebSocket 层能否跨用户读取；记录各回调载荷与对象存储上传流程
- [ ] exec SecretRef 加取密钥小程序、`openclaw secrets reload`、假模型服务驱动工具调用，全部跑通
- [ ] 外网模式的本机隧道和双向证书跑通，三类连接都经过隧道
- [ ] 锁定 MCP Python SDK 版本；ADR 附来源链接或上游提交，并更新 `docs/versions.md`

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
