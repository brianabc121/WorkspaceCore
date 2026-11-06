---
id: T-0132
title: Gmail 多邮箱按智能体分配：迁移 0041、分配接口、工具只看分配的邮箱、设置页与实例卡片、e2e
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,CLAUDE.md
depends_on: T-0131
created: 2026-11-06
updated: 2026-11-06
---

# T-0132 Gmail 多邮箱按智能体分配：迁移 0041、分配接口、工具只看分配的邮箱、设置页与实例卡片、e2e

## 目标
Gmail 支持连接多个邮箱，由管理员决定哪个邮箱分配给哪个智能体（原来是每个邮箱一个“允许智能体访问”，所有智能体一起开关）；所有设置都在网页上完成。

## 验收标准
- [x] 迁移 0041：`agent_mail_accounts`（复合外键保证同一工作空间），删除 `agents_allowed`；升级时原来允许的邮箱分配给现有全部实例，降级恢复 `agents_allowed`；迁移测试覆盖升级与降级
- [x] 接口：`PUT /api/v1/mail/accounts/{id}/agents`、`GET /api/v1/mail/agents`、实例 `PUT …/mail` 的 `account_ids`；两边 version 都加 1；审计；断开邮箱取消分配；角色与工作空间校验
- [x] MCP 工具只看得到分配给这个实例、没断开的邮箱；多个时要求 `account_id`
- [x] 连接时 `prompt=select_account consent`（核验 Google 文档原文后），方便连接第二个邮箱
- [x] 页面：设置 → 邮件 每个邮箱“分配给智能体”；实例卡片“邮件权限”勾选能用的邮箱；前端测试
- [x] e2e 场景 23：两个邮箱，按邮箱与按智能体分配，智能体只看到分配给自己的邮箱
- [x] 文档：MAIL_PLAN 第 10 节、例外七措辞、api-contract、data-dictionary 0041、threat-model、deploy-guide 8.7
- [ ] CI 全绿（含 agents 场景 23）

## 背景 / 约束 / 参考
- 人类 2026-11-06 在对话中要求：“这里的谷歌账号可以通过 workspace 平台的 web 端进行设置。接着 gmail 的邮箱需要具备多个邮箱的功能。管理员可以自己授权哪个邮箱分配给哪个智能体。以后改动的时候，需要设置的东西尽量放在 web 端。因为 ssh 终端的设置不适合政策用户操作”
- 方案：`docs/v2/MAIL_PLAN.md` 第 10 节；核验：`docs/decisions/2026-11-06-gmail-verification.md` 第 2 节（`prompt`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 13:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 13:14 UTC · claude · 开始任务
- 2026-11-06 13:14 UTC · claude · 人类在对话中指派（2026-11-06）：多邮箱 + 管理员按智能体分配 + 设置放在网页上。已实现迁移 0041、分配接口与工具过滤、设置页与实例卡片、e2e 场景 23 的两邮箱分配；‘设置尽量放在 web 端’记进 CLAUDE.md。CLAUDE.md 在 paths 里（Claude 专属说明由 claude 维护）。docs/AGENTS.md 例外七只改了一处措辞（‘邮箱的允许智能体访问’→‘管理员把这个邮箱分配给了这个实例’），按人类本次要求，越出 paths 的说明：docs 在 paths 内。
