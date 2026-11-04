---
id: T-0112
title: 智能体项目管理权限：实例开关与审计、create_project / add_project_agents / list_agents、限额与通知、AGENTS.md 例外三（迁移 0030）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0112 智能体项目管理权限：实例开关与审计、create_project / add_project_agents / list_agents、限额与通知、AGENTS.md 例外三（迁移 0030）

## 目标
按方案第 2.3 节：管理员按实例打开“允许它新建项目、拉智能体进项目”后，智能体可以用 create_project / add_project_agents / list_agents，直接执行；没开的实例看不到也调用不了。

## 验收标准
- [ ] 迁移 0030：实例的项目管理权限（谁开的、什么时候）；可降级
- [ ] 实例页开关（owner / admin，审计）；工具只对开了权限的实例列出；直接调用被拒（FORBIDDEN_CAPABILITY）并留记录
- [ ] `create_project`（项目知识库、项目群、群主是开权限的管理员）、`add_project_agents`（只限它建的项目）、`list_agents`；限额 5 / 20 每小时；通知管理员
- [ ] `docs/AGENTS.md` 第 8 节例外三；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
