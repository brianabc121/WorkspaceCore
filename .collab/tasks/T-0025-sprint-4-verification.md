---
id: T-0025
title: Sprint 4 版本核验：GitHub REST API（版本、细粒度 Token 最小权限、限流 / 分页 / ETag）与 AFFiNE 深链接（锁定 release、URL 结构、不调用未核验 API）（S4-01、S4-04）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/deployment.md,infra/affine
depends_on: T-0023
created: 2026-10-30
updated: 2026-10-30
---

# T-0025 Sprint 4 版本核验：GitHub REST API（版本、细粒度 Token 最小权限、限流 / 分页 / ETag）与 AFFiNE 深链接（锁定 release、URL 结构、不调用未核验 API）（S4-01、S4-04）

## 目标
Sprint 4 接入 GitHub（只读）并提供 AFFiNE 深链接。项目规则禁止猜测第三方 API 与版本：先核验 GitHub REST API 的版本、
认证方式与最小权限、限流 / 分页 / 条件请求的实际行为，以及 AFFiNE 锁定 release 的文档 URL 结构，写成 ADR，
作为 T-0026 契约和 T-0027 / T-0028 实现的依据。V1 对 AFFiNE 只做深链接与收藏，不调用任何未核验的 API、不抓取文档正文（规范 7.2、Sprint 4 DoD）。

## 验收标准
- [x] GitHub REST：锁定 API 版本（`X-GitHub-Api-Version`）；认证方式（细粒度 PAT 或 GitHub App）的选择与理由，读取仓库 / Issue / PR / Actions 所需的最小权限、过期与吊销；对应端点、分页（`Link` 头、`per_page` 上限）、过滤与字段
- [x] GitHub 限流：主限流与次级限流的响应（状态码、`x-ratelimit-*`、`retry-after`）、认证与未认证的额度、ETag / `If-None-Match` 返回 304 时是否计入额度；不需要 Token 的部分在真实 api.github.com 上实测并记录，需要 Token 的部分以官方文档为准并标注 → Connector 错误分类（RATE_LIMITED、AUTH_FAILED、NOT_FOUND 等）与 A17 的退避策略
- [x] GitHub 字段映射：仓库 / Issue / PR / Actions 运行 → 统一外部资源（外部 ID 的写法由本 ADR 给出，内部仍经 mapping，不拼接进 SQL，规范第 6 节）；V1 是否启用 GitHub Webhook（规范 7.3：启用则验证签名），不启用时以周期同步为准
- [x] AFFiNE：锁定一个稳定 release（附镜像 tag 与 digest、许可证）；按该 tag 的源码 / 官方文档核验工作空间与文档的 URL 结构，形成深链接的校验规则（scheme、主机与配置的 AFFiNE 外部地址一致、路径格式；禁止 `javascript:` 等脚本 URL）；写明 V1 不调用 AFFiNE 的任何 API，全文索引 / 原生编辑的 Spike 留到以后（规范 7.2、第 16 节）
- [x] 如果为了核验 URL 结构需要运行 AFFiNE 实例，只用临时环境；是否提供 `infra/affine/` 的可选 Compose 由 ADR 决定，提供时不照搬官方模板里依赖私有网络的数据库 trust 认证（规范第 17 节）；`docs/deployment.md` 写明 AFFiNE 外部地址的配置方式
- [x] 所有结论附来源链接（官方文档、源码 tag、实测记录）；无法核验的项明确标为未验证，后续以接口桩处理

## 背景 / 约束 / 参考
- 规范：第 2 节（版本要求）、第 4.2 节（数据所有权）、第 7 节与 7.2 / 7.3、第 16 节（风险）、第 17 节（官方来源与核验清单）、第 18 节
- 联网核验与 Docker 由 claude 负责（D-0003）。人类批准进入 Sprint 4 时（2026-10-30）未指定 GitHub 账号、组织或 AFFiNE 实例，由 claude 选定并在 ADR 记录；不在对话里向人类索取 Token
- 参照 Sprint 3 的同类 ADR：`docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 16:43 UTC · claude · 创建任务（owner: claude）
- 2026-10-30 16:51 UTC · claude · 开始任务
- 2026-10-30 16:58 UTC · claude · 核验完成：docs/decisions/2026-10-30-github-rest-and-affine-verification.md。GitHub 锁定 REST API 2026-03-10（另一个支持版本 2022-11-28 于 2028-03-10 停止支持），细粒度 PAT，只读 Metadata / Issues / Pull requests / Actions（每个端点的权限用响应头 X-Accepted-GitHub-Permissions 实测，与官方数据一致）；分页用 Link 原样翻页；ETag 条件请求返回 304 不计额度（实测）；主 / 次级限流的 403 / 429 退避顺序按文档；V1 不启用 GitHub Webhook。AFFiNE 锁定 0.27.4（ghcr digest sha256:b649f5ce…3099，2026-08-18），深链接按 tag 源码的路由 /workspace/<ws>/<doc> 与 /share/<ws>/<doc> 校验，V1 不调用 AFFiNE 任何接口；不提供 infra/affine（官方 Compose 用 trust 认证、镜像未锁定，本环境也下载不到镜像层），部署文档第 9、10 节写明 AFFiNE 与 GitHub 的配置要点。限制：本环境的网络策略拒绝 docs.github.com、docs.affine.pro，改读官方 github/docs 仓库源文件与官方 OpenAPI；api.github.com 只能访问本仓库且代理会替换 Authorization，所以未认证 / PAT 额度、401、限流响应以文档为准，CI 用模拟服务验证（T-0032）。
- 2026-10-30 16:58 UTC · claude · status in_progress → done：GitHub REST 2026-03-10 与 AFFiNE 0.27.4 核验 ADR 完成，T-0026 契约可以开始
