---
id: T-0131
title: Gmail 验收与文档：e2e（真实 OpenClaw + 假 Gmail）、契约与部署文档、人工验收清单
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e,infra
depends_on: T-0129, T-0130
created: 2026-11-06
updated: 2026-11-06
---

# T-0131 Gmail 验收与文档：e2e（真实 OpenClaw + 假 Gmail）、契约与部署文档、人工验收清单

## 目标
在真实 OpenClaw 上验收智能体的邮件工具，补齐文档与人工验收清单。

## 验收标准
- [x] e2e：真实 OpenClaw + 假 Gmail：没开查询看不到工具；打开后经模型调用 `mail_search`；没开发送时 `mail_send` 被拒；打开并限定收件人后发出一封，假 Gmail 收到正确的 raw
- [x] 文档：api-contract、data-dictionary、threat-model、deploy-guide（Google Cloud OAuth 客户端的配置步骤、回调地址、测试中状态 7 天过期的提醒、人工验收清单）
- [x] 核验记录第 6 节：人工验收结果（需要人类的 Google 账号；没做时如实标明）——已如实标明“真实 Google 账号：尚未核验”，清单在 deploy-guide 8.7

## 背景 / 约束 / 参考
- 方案：`docs/v2/MAIL_PLAN.md`；核验：`docs/decisions/2026-11-06-gmail-verification.md`
- 人类 2026-11-06 在对话中提出：“再给我为 workspace 平台新增可以收发 gmail 邮件的功能……智能体收发 / 查询邮件的权利也需要做成开关，由管理员进行赋予。默认仅可以查询邮件，不可以发送邮件”；并选定智能体发信“直接发出（新增例外七）”、接入方式“Gmail API + OAuth”。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 11:00 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 11:48 UTC · claude · 文档已补（api-contract、data-dictionary 迁移 0040、threat-model 四行、deploy-guide 8.7）。剩：agents CI 里真实 OpenClaw + 假 Gmail 的场景（tests/e2e 需要一个假 Gmail 服务与假模型的工具调用脚本），以及核验记录第 6 节的人工验收（要人类的 Google 账号）
- 2026-11-06 12:44 UTC · claude · 开始任务
- 2026-11-06 12:44 UTC · claude · e2e：tests/e2e/fake_gmail_server.py（模拟 Google，复用 tests/fake_google.py）、compose 的 GOOGLE_OAUTH_TOKEN_URL / GOOGLE_OAUTH_REVOKE_URL / GMAIL_API_URL（默认真实地址）、ci-infra agents 任务把它们指向 fake-gmail、agents_acceptance.py 场景 23。启用中途打开的 mail_send 按场景 18 / 22 的做法直接从实例的服务器调 MCP（不经模型）；mail_search 经真实 OpenClaw + 假模型调用。核验记录第 6 节如实写明真实账号尚未核验
- 2026-11-06 12:44 UTC · claude · 越出 paths 的两处：compose.yaml（给 api 传三个 Google 地址，默认值不变）、.github/workflows/ci-infra.yml（agents 任务的 .env 指向模拟服务）。都是让 CI 能跑场景 23 所必需；没有别的进行中任务占用这两个文件
- 2026-11-06 13:15 UTC · claude · status in_progress → done：PR #160 合并（bdfe76b）：CI 运行 42220022912 的 agents 任务（场景 23，真实 OpenClaw + 模拟 Google）与其余 10 项检查全部通过。真实 Google 账号的人工验收需要人类，核验记录第 6 节如实写着尚未核验。
