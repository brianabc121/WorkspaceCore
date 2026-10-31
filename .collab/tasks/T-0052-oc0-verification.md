---
id: T-0052
title: OC-0 核验与选型：OpenClaw extended-stable、openclaw-channel 兼容性、OpenIM 部署与接口、exec 密钥、外网隧道、假模型（S6-01）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/versions.md,infra/openclaw,infra/openim
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0052 OC-0 核验与选型：OpenClaw extended-stable、openclaw-channel 兼容性、OpenIM 部署与接口、exec 密钥、外网隧道、假模型（S6-01）

## 目标
在锁定版本的真实实例上核验改造方案依赖的上游能力（方案第 9、10 节），把不确定项变成有来源的结论；核验不了的写成接口桩和 TODO。

## 验收标准
- [x] 锁定 OpenClaw extended-stable 版本与镜像 digest；`openclaw-channel` 在该版本上能收发私聊和群聊，结论写进 ADR
- [x] OpenIM v3.8.3-patch.18 按 `infra/openim/` 初稿启动，全部镜像锁 digest，实测内存与磁盘（bitnamilegacy 替代镜像的评估移到 OC-3：要和自建补丁镜像一起改 compose 并验证）
- [x] 抓包列出插件用到的 OpenIM REST 接口，作为接入网关的白名单；核验 WebSocket 层能否跨用户读取（能，已写越权回归脚本）；记录回调载荷（对象存储上传流程移到 OC-3：插件的发图工具在 2026.9.36 上注册不了，要和管理员发图一起实现时再测）
- [x] exec SecretRef 加取密钥小程序、`openclaw secrets reload`、假模型服务驱动工具调用，全部跑通
- [x] ~~外网模式的本机隧道和双向证书跑通~~：移到 OC-2（T-0054），和接入入口、内部 CA 一起实现与验证
- [x] 锁定 MCP Python SDK 版本；ADR 附来源链接或上游提交，并更新 `docs/versions.md`

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 16:30 UTC · claude · 开始任务
- 2026-10-31 16:32 UTC · claude · 开工。已确认：extended-stable 维护线当前为 v2026.9.33 ~ 9.36（最新 9.36）；镜像 openclaw/openclaw:2026.9.36（Docker Hub）与 ghcr.io/openclaw/openclaw:2026.9.36 的 digest 相同，均为 sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f；npm 上 @openim/openclaw-channel 最新为 0.3.3（GitHub tag 只到 v0.3.2）。ghcr 的镜像层下载被本会话网络策略拦截，改用 Docker Hub；Docker Hub 对 mongo、bitnamilegacy/minio 返回 429，稍后重试
- 2026-10-31 17:09 UTC · human · 人类在对话中决定：同意自建打过补丁的 OpenIM 镜像（修复消息网关和 REST 越权：内层 UserID 不校验），不向 OpenIM 上游报告
- 2026-10-31 17:24 UTC · claude · OC-0 完成，结论见 docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md。关键发现：OpenIM 官方镜像的 REST 与 WebSocket 都能被任意实例令牌越权读取管理员会话（已写回归脚本 infra/openim/probes/，人类同意自建补丁镜像、不上报）；插件智能体工具在 2026.9.36 上注册不了（实例第一版只回文字）；签发新 IM 令牌会踢下线；secrets reload 能热更新模型密钥；MCP 经 Tool Search 调用、结果被标记为不可信、tools.deny 对间接调用有效。外网隧道、对象存储、bitnamilegacy 替换移到 OC-2 / OC-3（已写进 T-0054、T-0055 的验收标准）
- 2026-10-31 17:24 UTC · claude · status in_progress → done：OC-0 核验完成，ADR 与 infra 初稿已提交
