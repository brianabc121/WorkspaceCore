---
id: T-0158
title: 插拔式模块（一）：方案、ADR 与新增模块指南（给以后的代理照着加功能）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs,CLAUDE.md,.claude,scripts,Makefile
depends_on:
created: 2026-11-08
updated: 2026-11-08
---

# T-0158 插拔式模块（一）：方案、ADR 与新增模块指南（给以后的代理照着加功能）

## 目标
人类 2026-11-08 要求按“工作台插拔式扩展设计”（https://claude.ai/artifact/AqLi7VYg8UaQVmpWxfibmj）落实代码，
并补全文档，让以后的代理新增功能时能直接照文档插拔。本任务负责方案落库、ADR、开发指南、脚手架和代理入口说明。

## 验收标准
- [ ] `docs/v2/MODULES_PLAN.md`：方案（含人类未填的 D1~D7 按建议执行的记录）
- [ ] `docs/decisions/` 下的 ADR：模块清单、注册表、启用表、迁移仍是一条链等决定
- [ ] `docs/modules/README.md`：新增 / 停用 / 移除模块的分步指南、清单字段说明、检查清单
- [ ] `services/api/app/modules/README.md`、`apps/web/src/modules/README.md`：代码旁的简要说明，指向指南
- [ ] `make module-new key=... name=...` 脚手架生成前后端骨架并登记到注册表
- [ ] CLAUDE.md 指向指南；`.claude/skills/new-module/SKILL.md` 给 Claude 代理用

## 背景 / 约束 / 参考
方案：https://claude.ai/artifact/AqLi7VYg8UaQVmpWxfibmj（落库后见 `docs/v2/MODULES_PLAN.md`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-08 12:57 UTC · claude · 创建任务（owner: claude）
- 2026-11-08 12:58 UTC · claude · 开始任务
