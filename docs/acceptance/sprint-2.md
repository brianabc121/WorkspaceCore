# Sprint 2 验收报告（进行中）

- 验收人：claude（协作任务 T-0017，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30
- 状态：**接口级部分已完成并纳入 CI；浏览器部分等待 T-0016（Sprint 2 前端，codex）合并后再做。**
  本报告随 T-0017 逐步补全，全部完成前不勾选 `docs/TASKS.md`，也不请求进入 Sprint 3。

## 1. Sprint 2 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 拖拽后刷新布局不丢 | 接口级通过；浏览器未运行 | 全栈脚本 `tests/e2e/sprint2_acceptance.py`：保存布局后在同一会话和另一个会话重新读取首页，布局与保存的完全一致、版本号 +1。拖拽本身属于前端（T-0016），合并后用浏览器 E2E 验证 |
| 未知 widget type 安全报错 | 接口级通过；前端未运行 | 全栈脚本：创建组件时未知类型、非 http(s) 链接、多余字段均为 `422 VALIDATION_FAILED`，错误里不回显输入、没有堆栈。前端按自己的 Registry 显示安全的错误卡片（契约 ADR 第 4 节），T-0016 合并后用浏览器验证 |
| 部分 Widget 失败不影响整页 | 接口级通过；前端未运行 | 全栈脚本：库里写入一条类型已不支持、配置不是对象、布局无法解析的组件后，首页聚合与组件列表仍然 200；该组件原样标出类型、配置为空、放到最下方，其余组件的布局与内容不变。任务模块不可用时其余组件照常返回（A05）。pytest：provider 出错或超时只把该模块标为 unavailable |

## 2. 验收场景（规范第 16 节）

全栈环境：Compose 6 个服务，经 Caddy 对外入口访问。接口级场景由 `tests/e2e/sprint2_acceptance.py` 自动化，
**已纳入 CI**（`ci-infra.yml` 的 “Sprint 2 全栈验收” 步骤，在 Sprint 1 全栈验收之后运行）。
脚本不要求空库：每次运行都用新的邮箱，通过 `docker compose exec api` 用应用自身的代码写入测试用户、工作空间与默认首页。

| 场景 | 期望 | 结果 |
|---|---|---|
| A03 viewer 修改 Widget | 返回 403 | 通过：viewer 可以查看首页；修改组件配置、添加、删除组件、保存布局、新建仪表盘均为 `403 FORBIDDEN`，数据未被改动；member 做同样的修改返回 200（确认 403 来自角色） |
| A04 保存并刷新首页布局 | 布局一致，版本号更新 | 通过：见第 1 节 |
| A05 第三方服务断开 | 首页显示 stale/unavailable，其余模块可用 | 接口级通过：本 Sprint 尚无 Connector，`modules.tasks` 为 `unavailable / not_configured`，汇总值为 `null`（不以 0 冒充），`today_tasks` 为空，笔记与快捷链接照常返回。真实的 Connector 断开在 Sprint 3 接入 Vikunja 后验证；页面上的降级展示在 T-0016 合并后用浏览器验证 |
| 版本冲突（S2-04） | 旧版本保存被拒绝，不覆盖别人的改动 | 通过：另一个会话用旧版本保存布局 → `409 VERSION_CONFLICT`，`details.current_version` 为最新版本；用旧版本修改组件同样 409；缺少组件的布局 → 422；被拒绝的请求没有改动数据 |
| 跨工作空间（沿用 S1-03） | 非成员 404 | 通过：非成员读取首页、修改组件，以及用自己的工作空间读取别人的仪表盘组件、删除别人的组件，均为 `404 NOT_FOUND` |
| 审计日志（契约 ADR 第 12 节） | 记录布局与组件变化，不记录内容 | 通过：`dashboard.updated`、`widget.created`、`widget.updated`、`widget.deleted` 均已记录，日志里不含笔记文本 |

全栈脚本共 38 项检查，本机在全新数据卷上与 Sprint 1 脚本按 CI 顺序运行全部通过，在同一个库上重复运行也全部通过；
首页聚合耗时 0.07 秒（上限 3 秒）。

### 浏览器（S2-05，未运行）

等待 T-0016 合并。计划用 `tests/e2e` 已锁定版本的 Playwright 验证：首页加载 → 编辑模式拖拽 → 保存 → 刷新后布局一致；
未知类型组件显示错误卡片、其余组件正常；模块 unavailable 时的降级展示；并把 Sprint 1 的浏览器流程一起纳入 CI。
Sprint 1 的浏览器流程暂不放进 CI：T-0016 会改动顶栏和侧栏，提前放进去可能让 T-0016 的 PR 因测试选择器不匹配而失败。

## 3. 任务清单（docs/TASKS.md Sprint 2）

| 条目 | 状态 | 说明 |
|---|---|---|
| S2-01 Dashboard/Widget 数据表及读写 API | 后端已完成（T-0014 契约 PR #20、T-0015 后端 PR #21） | 迁移 0004；全栈脚本复验 |
| S2-02 Vue 顶栏、侧栏、响应式页面、dark/light theme | 等待 T-0016 | 前端 |
| S2-03 Widget registry/schema、基础卡片 | 后端已完成；前端等待 T-0016 | 服务端 Registry 与 422 已验证 |
| S2-04 Grid 拖拽/布局保存/版本冲突处理 | 后端已完成；前端等待 T-0016 | 布局保存与 409 已验证 |
| S2-05 聚合接口、loading/error/stale、E2E | 聚合接口与接口级 E2E 已完成；页面状态与浏览器 E2E 等待 T-0016 | |

## 4. 本阶段执行的命令

```
# 全新数据卷的独立 Compose 项目（避免影响已有数据），端口 18080
$ COMPOSE_PROJECT_NAME=ws-accept HTTP_PORT=18080 CORS_ALLOWED_ORIGINS=http://127.0.0.1:18080 docker compose up -d --wait
$ make migrate
Running upgrade  -> 0001 / 0001 -> 0002 / 0002 -> 0003 / 0003 -> 0004
$ python3 tests/e2e/sprint1_acceptance.py --base-url http://127.0.0.1:18080
全部通过
$ python3 tests/e2e/sprint2_acceptance.py --base-url http://127.0.0.1:18080
通过  A03 viewer 修改 Widget 配置 → 403（403 FORBIDDEN）
通过  A04 同一会话刷新后布局与保存的一致，版本号已更新
通过  版本冲突：另一个会话用旧版本保存 → 409 VERSION_CONFLICT，返回最新版本（409 VERSION_CONFLICT {'current_version': 2}）
通过  DoD 库里有一条已损坏的组件时首页聚合仍然 200（200）
…（共 38 项）
全部通过
```
