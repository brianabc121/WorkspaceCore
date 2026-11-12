---
id: T-0223
title: 知识库图片存储（R2 / OSS）改为在网页上按工作空间配置
owner: claude
status: in_progress
priority: P1
branch: b6-model-connections
paths: services/api/app,services/api/migrations,services/api/tests,services/api/openapi.json,apps/web/src,apps/web/tests,scripts/deploy.sh,compose.yaml,.env.example,docs,tests/e2e,.github/workflows/ci-infra.yml
depends_on:
created: 2026-11-12
updated: 2026-11-12
---

# T-0223 知识库图片存储（R2 / OSS）改为在网页上按工作空间配置

## 目标
人类 2026-11-12 建好了 Cloudflare R2 桶，要求“这里的配置直接改到可以通过 web 端的 workspace 进行配置”。
原来知识库图片（与常驻环境快照）的主存储、副本和 R2 / OSS 连接信息写在 `.env`，由 `deploy.sh connect` 在 SSH 终端里问；
改成每个工作空间在网页“设置 → 图片存储”里设置，保存前自检，不再要求改 `.env` 或跑部署脚本。

## 验收标准
- [x] 表 `storage_settings`（迁移 0060）按工作空间保存主存储、副本、R2 / OSS 连接信息；密钥用 `CredentialBox` 加密（用途绑定工作空间与字段），凭证轮换覆盖
- [x] `GET / PUT /api/v1/settings/storage`：owner 修改、admin 只读、member 403，按工作空间隔离；版本冲突 409；密钥不返回，`null` 保持、`""` 清除
- [x] 地址只由账户 ID / 辖区 / 地域 / 桶名拼出 R2、OSS 官方域名，填别的主机名被拒绝；保存前对主存储与副本自检，不通过不保存并返回原因
- [x] 还存着本工作空间图片或快照的存储不能清空（409 STORAGE_IN_USE）；审计不含密钥
- [x] 上传、读取、复制、清理、自检、快照都按工作空间的设置取存储；运维页按工作空间显示
- [x] 前端“设置 → 图片存储”页面（侧栏“管理”）、运维页链接到设置页；vitest 覆盖保存、自检失败、密钥不回显、清空、冲突、只读
- [x] 去掉 `.env.example`、`compose.yaml` 的存储变量与 `deploy.sh connect` 的存储一步、`connect storage-check`
- [x] 文档（部署手册第 19 节、部署指南、接口契约、威胁模型、架构、ADR）更新；浏览器验收 `storage_browser.cjs` 接入 Compose CI

## 背景 / 约束 / 参考
- 决策：`docs/decisions/2026-11-12-storage-settings-per-workspace.md`
- 不需要向后兼容（还没有生产数据），旧 `.env` 的值不迁移；`R2_ENDPOINT` / `R2_REGION` / `OSS_ENDPOINT` 只给测试指向 Garage

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-12 06:40 UTC · claude · 创建任务（owner: claude）
- 2026-11-12 06:40 UTC · claude · 开始任务
