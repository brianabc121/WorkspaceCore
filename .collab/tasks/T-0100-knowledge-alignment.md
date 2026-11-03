---
id: T-0100
title: 知识库认知对齐与自我提升（项目简报已读、复盘定时任务、纠正回写、指标）与验收（K-5 + K-6）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,tests/e2e,.github/workflows,docs,scripts
depends_on: T-0099
created: 2026-11-03
updated: 2026-11-03
---

# T-0100 知识库认知对齐与自我提升（项目简报已读、复盘定时任务、纠正回写、指标）与验收（K-5 + K-6）

## 目标
完成 `docs/v2/KNOWLEDGE_PLAN.md` 的 K-5 与 K-6：
- 项目简报的已读记录与提醒（管理员以自己的 IM 账号发）；
- 每晚复盘定时任务：SSH 部署时用 `openclaw automations add` 建；
- 写入准则；智能体指标；
- 浏览器与真实 OpenClaw 验收，文档与威胁模型。

## 验收标准
- [x] 简报已读、谁没读最新版、“现在发给它”
- [x] 复盘定时任务随 SSH 部署创建（已有就更新，用 `--declaration-key`），CHECKLIST 给出手工命令
- [x] 智能体知识库页的指标（近 30 天）与近 8 周趋势
- [x] CI 的真实 OpenClaw 经 MCP 调用 knowledge_write / search / delete，直接生效、可撤销、超限暂停、密钥拦截（`agents_acceptance.py` 场景 15）
- [x] 浏览器验收脚本覆盖知识库主要路径（`tests/e2e/knowledge_browser.cjs`，CI 的 compose 任务）
- [x] docs：threat-model、architecture、deploy-guide、versions；方案状态改为已完成

## 背景 / 约束 / 参考
- 方案：`docs/v2/KNOWLEDGE_PLAN.md`（已批准）；核验：`docs/decisions/2026-11-03-knowledge-k0-verification.md`
- 效果图：https://claude.ai/artifact/Lib1PcRket1qH18Bo5YwBv

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 20:45 UTC · claude · 创建任务（owner: claude）
- 2026-11-03 23:13 UTC · claude · 开始任务
- 2026-11-03 23:21 UTC · claude · 完成：A 后端——参与项目的简报未读时 knowledge_* 的 notices 提醒（最多 3 条，只带 project_id 与版本，不含人填的名字）；MCP get_task 输出 brief（版本、读到的版本、是否未读）；GET /knowledge/spaces/{id}/activity（近 8 周人 / 智能体的修改）与 AgentKnowledgeOut.weekly（读自己的 / 读共享的 / 写入）；接入包新增 reflection-prompt.md 与 setup-reflection.sh（openclaw automations add --declaration-key，独立会话、不投递，重复运行只更新）；SSH 部署在 online 之后增加 reflection 步骤（失败记 skipped，不算安装失败）；AGENT_REFLECTION_CRON / AGENT_REFLECTION_TIMEZONE（启动时校验，防止写进脚本的值带引号或 $）。B 前端——项目知识库主页“每周的修改”柱状图、智能体主页“每周趋势”（读取：共享 / 自己的；写入），配色在亮暗两种卡片底色上跑过 dataviz 校验，带图例、悬停提示、表格视图；管理员对没读最新简报的智能体“现在发给它”（经工作台 IM 接口、管理员自己的账号、同一版简报同一个 Idempotency-Key）。C 验收——agents_acceptance.py 场景 15（真实 OpenClaw：写入不审批、检索读取计入趋势、密钥拦截、人修改与一键撤销写回纠正、简报已读、第 6 次删除自动暂停并通知、setup-reflection.sh 建任务且重复运行不重复、立即运行一次复盘且工具不放宽、不投递）；agent_ssh_acceptance.py 检查复盘任务建好、重新部署只更新；tests/e2e/knowledge_browser.cjs（本机真实后端已跑通）进 ci-infra compose。文档：api-contract、deploy-guide、deployment、threat-model、architecture、versions、K-0 核验第 5 节、方案状态“已完成”。偏差：K-0 3.3 原定“先查同名再跳过”，实测有 --declaration-key，改为直接更新；--tools 与 Tool Search 的配合没核验，暂不收紧（ADR 第 5 节 TODO）。
- 2026-11-03 23:22 UTC · claude · status in_progress → done：验收标准全部满足，随 PR 合并生效（与 T-0099 同一个 PR #143）
