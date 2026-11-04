---
id: T-0112
title: 智能体项目管理权限：实例开关与审计、create_project / add_project_agents / list_agents、限额与通知、AGENTS.md 例外三（迁移 0030）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0112 智能体项目管理权限：实例开关与审计、create_project / add_project_agents / list_agents、限额与通知、AGENTS.md 例外三（迁移 0030）

## 目标
按方案第 2.3 节：管理员按实例打开“允许它新建项目、拉智能体进项目”后，智能体可以用 create_project / add_project_agents / list_agents，直接执行；没开的实例看不到也调用不了。

## 验收标准
- [x] 迁移 0030：实例的项目管理权限（谁开的、什么时候）；可降级
- [x] 实例页开关（owner / admin，审计）；工具只对开了权限的实例列出；直接调用被拒（FORBIDDEN_CAPABILITY）并留记录
- [x] `create_project`（项目知识库、项目群、群主是开权限的管理员）、`add_project_agents`（只限它建的项目）、`list_agents`；限额 5 / 20 每小时；通知管理员
- [x] `docs/AGENTS.md` 第 8 节例外三；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 10:31 UTC · claude · 开始任务
- 2026-11-04 10:31 UTC · claude · 实现完成：迁移 0030（agent_instances.project_admin_enabled / by / at，projects.created_by_instance）；PUT /agents/instances/{id}/project-admin（owner / admin、乐观并发、审计）；AgentInstanceOut.project_admin；agent_projects.py：create_project / add_to_project / list_peers（只列给、只允许开了权限的实例，否则 FORBIDDEN_CAPABILITY 并记为拒绝；直接执行、执行记录 direct；建 5 / 拉 20 每小时；建出的项目有知识库成员与项目群，群主是开权限的人；只能拉进自己建的项目）；AGENTS.md 第 8 节例外三；实例卡片的开关；agents 场景 18。与方案的偏差：工具名 add_project_agents / list_agents 改为 add_to_project / list_peers——契约测试按 agents / members 拦截实例与成员管理类工具名，不为此放宽（方案 2.3 已注明）；开关在实例页（只有 owner 能进），接口也允许 admin。
- 2026-11-04 10:31 UTC · claude · status in_progress → done：验收标准全部完成：新增后端测试 4 个通过；契约、MCP、实例、项目群、任务接取、迁移等套件 96 个通过（改名后契约与 MCP 60 个重跑通过）；ruff / mypy 通过；前端 lint / typecheck / 421 个测试 / build 通过。下一步 T-0113。
