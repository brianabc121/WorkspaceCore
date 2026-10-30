---
id: T-0018
title: Sprint 3 版本核验：锁定 Vikunja release、实例 OpenAPI、认证范围、Webhook 签名与字段映射（S3-01）
owner: claude
status: todo
priority: P1
branch:
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
- [ ] 锁定 Vikunja 的具体 release 与镜像 tag（附 digest），不使用 `latest` / `stable`；核验许可证与默认配置的安全性（注册开关、CORS、JWT 密钥、数据库）
- [ ] `infra/vikunja/`：开发与 CI 用的独立 Compose（固定镜像、随机密钥、持久卷、健康检查），与工作台通过受控网络连通；`Makefile` 增加启动 / 停止入口，`docs/deployment.md` 写明用法
- [ ] 在运行实例上读取 OpenAPI 并存档（规范 7.1 要求 API v2：`/api/v2/openapi.json`；锁定版本若没有 v2，记录实际情况并 escalate 给人类决定，不臆测）
- [ ] ADR：认证方式与最小权限（API Token 的权限范围、过期、吊销）；项目 / 任务的端点、分页、过滤、字段、优先级与标签的允许值；Webhook 事件类型、签名算法与请求头、送达与重试行为；错误码与限流 → Connector 错误分类（规范第 7 节）
- [ ] 字段映射：Vikunja 项目 / 任务 → 统一 ProjectDTO / TaskDTO（规范 6.3，外部 ID 形如 `vikunja:task:<id>`）；`tested_version` 写入 integration 元数据的约定
- [ ] 所有结论附来源链接（官方文档、源码 tag、实例 OpenAPI 存档）；无法核验的项明确标为未验证，后续以接口桩处理

## 背景 / 约束 / 参考
- 规范：`docs/DEVELOPMENT_SPEC.md` 第 2 节（版本要求）、第 7 节与 7.1（Vikunja）、第 10 节、第 11 节（部署）、第 17 节（官方来源）、第 18 节
- 联网核验与 Docker 由 claude 负责（D-0003）；人类未指定 Vikunja 实例或版本（2026-10-30 批准进入 Sprint 3 时），由 claude 选当前稳定 release

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 13:52 UTC · claude · 创建任务（owner: claude）
