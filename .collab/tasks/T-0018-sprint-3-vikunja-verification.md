---
id: T-0018
title: Sprint 3 版本核验：锁定 Vikunja release、实例 OpenAPI、认证范围、Webhook 签名与字段映射（S3-01）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,infra/vikunja,Makefile,docs/deployment.md
depends_on: T-0017
created: 2026-10-30
updated: 2026-10-30
---

# T-0018 Sprint 3 版本核验：锁定 Vikunja release、实例 OpenAPI、认证范围、Webhook 签名与字段映射（S3-01）

## 目标
Sprint 3 接入第一个真实的第三方服务 Vikunja（任务主源）。项目规则禁止猜测第三方 API 与版本：
先锁定一个具体的 Vikunja release，在运行实例上读取它自己的 OpenAPI，把认证、端点、字段、Webhook 签名等核验结果写成 ADR，
作为 T-0019 契约和 T-0020 / T-0021 实现的依据。同时提供开发与 CI 用的独立 Vikunja Compose（规范第 11 节：Vikunja 单独 Compose 安装）。

## 验收标准
- [x] 锁定 Vikunja 的具体 release 与镜像 tag（附 digest），不使用 `latest` / `stable`；核验许可证与默认配置的安全性（注册开关、CORS、JWT 密钥、数据库）
- [x] `infra/vikunja/`：开发与 CI 用的独立 Compose（固定镜像、随机密钥、持久卷、健康检查），与工作台通过受控网络连通；`Makefile` 增加启动 / 停止入口，`docs/deployment.md` 写明用法
- [x] 在运行实例上读取 OpenAPI 并存档（规范 7.1 要求 API v2：`/api/v2/openapi.json`；锁定版本若没有 v2，记录实际情况并 escalate 给人类决定，不臆测）
- [x] ADR：认证方式与最小权限（API Token 的权限范围、过期、吊销）；项目 / 任务的端点、分页、过滤、字段、优先级与标签的允许值；Webhook 事件类型、签名算法与请求头、送达与重试行为；错误码与限流 → Connector 错误分类（规范第 7 节）
- [x] 字段映射：Vikunja 项目 / 任务 → 统一 ProjectDTO / TaskDTO（规范 6.3，外部 ID 形如 `vikunja:task:<id>`）；`tested_version` 写入 integration 元数据的约定
- [x] 所有结论附来源链接（官方文档、源码 tag、实例 OpenAPI 存档）；无法核验的项明确标为未验证，后续以接口桩处理

## 背景 / 约束 / 参考
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 2 节（版本要求）、第 7 节与 7.1（Vikunja）、第 10 节、第 11 节（部署）、第 17 节（官方来源）、第 18 节
- 联网核验与 Docker 由 claude 负责（D-0003）；人类未指定 Vikunja 实例或版本（2026-10-30 批准进入 Sprint 3 时），由 claude 选当前稳定 release

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 13:58 UTC · claude · 开始任务
- 2026-10-30 14:12 UTC · claude · 核验完成：锁定 Vikunja 2.7.1（digest sha256:f7dc8428…4576，AGPL-3.0，revision 5619c4a9）；实例提供 API v2（/api/v2/openapi.json，OpenAPI 3.1.0，137 路径），存档 infra/vikunja/openapi/vikunja-2.7.1-api-v2.json。实测：受限 API Token（expires_at 必填；越权/失效都是 401 code 11）；任务增改查与 merge-patch；写入不校验 If-Match（无乐观并发）；无幂等键；删除后接口里整条消失（404 code 4002），tombstone 只能靠 task.deleted 或全量差集；filter=updated/created 可用于增量同步；Webhook 按项目注册，X-Vikunja-Signature=HMAC-SHA256(原始请求体)，无时间戳与投递 ID，失败约 2.3 秒内 6 次后丢弃；默认 SSRF 防护拦截私有地址；限流默认关闭、开启后 429+Retry-After。ADR docs/decisions/2026-10-30-vikunja-2.7.1-verification.md。部署：infra/vikunja/compose.yaml（只读根文件系统、关闭注册与 CORS、SQLite 卷、volume-init 改属主）+ make vikunja-up/-user/-down/-logs + docs/deployment.md 第 8 节；本机实测 api/worker 经 integrations 网络访问 Vikunja、Webhook 经 caddy 送达 api、Vikunja 解析不到 postgres/redis。越出 paths 的文件（均为 claude 负责的平台文件）：compose.yaml（新增内部网络 integrations，caddy/api/worker 加入）、.env.example（VIKUNJA_* 变量）、ci-infra.yml（校验 Vikunja Compose 配置）。
- 2026-10-30 14:12 UTC · claude · status in_progress → done：S3-01 版本核验完成；T-0019 契约可以开始
