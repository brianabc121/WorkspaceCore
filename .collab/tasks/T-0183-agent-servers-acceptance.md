---
id: T-0183
title: 智能体服务器（六）：真实 SSH / Docker 一机两实例验收、浏览器与 Runner 回归、文档
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github/workflows/ci-infra.yml,docs
depends_on: T-0182
created: 2026-11-09
updated: 2026-11-09
---

# T-0183 智能体服务器（六）：真实 SSH / Docker 一机两实例验收、浏览器与 Runner 回归、文档

## 目标
在真实 SSH / Docker 上验收密码连接、初始化与一机两实例部署，回归 Runner 与浏览器；更新文档。
方案见 `docs/v2/agent-servers-redesign/`（设计规范、接口与迁移约定、六阶段计划、两张效果图）；人类在对话中要求“根据效果图和开发文档全面落实”。（计划 Task 6）

## 验收标准
- [x] `agent_ssh_acceptance.py`：密码登录、authorized_keys 不新增平台公钥、初始化后无实例、同一服务器部署甲乙并都接入；重部署 / 卸载甲不影响乙
- [x] 旧 key-only 服务器补密码后定向移除旧公钥，其他公钥保留；密码错误、指纹变化、同机忙碌返回真实状态
- [x] `agent_ssh_browser.cjs` 走两个独立页面的流程；Runner 的 SSH 验收继续通过
- [x] api-contract、data-dictionary、threat-model、部署文档、MULTI_OPENCLAW_PLAN 更新；核验记录写进 docs/verification

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 06:30 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 09:19 UTC · claude · 开始任务
- 2026-11-09 09:19 UTC · claude · 验收脚本、浏览器脚本与文档已写（3fac93e 等），按核验修了三处业务问题（5c3ae8e）；等 CI agents job 在真实 sshd + Docker 上跑
- 2026-11-09 10:17 UTC · claude · status in_progress → done：CI 42592666997 全绿（agents：SSH 验收全部通过、两页浏览器验收与 Runner 浏览器验收全部通过）；核验记录第 6 节已填
