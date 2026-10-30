# Sprint 2 验收报告

- 验收人：claude（协作任务 T-0017，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30
- 结论：**Sprint 2 的 DoD 全部通过**；“未运行”项不属于 Sprint 2 范围或受环境限制，已逐条说明。
- 是否进入 Sprint 3：等待人类批准（D-0003 / D-0004：进入下一个 Sprint 由人类决定）。

## 1. Sprint 2 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 拖拽后刷新布局不丢 | 通过 | 浏览器 `tests/e2e/sprint2_browser.cjs`：真实后端上用鼠标拖动便签、拖动右下角改变宽度 → 保存 → 后端存下的四个组件位置与页面网格逐一相同，版本 +1，移动端顺序不变 → 刷新后网格位置与保存前完全一致。接口级 `tests/e2e/sprint2_acceptance.py`：保存后在同一会话与另一个会话重新读取都一致 |
| 未知 widget type 安全报错 | 通过 | 写入：未知类型、`javascript:` 链接、多余字段都返回 `422 VALIDATION_FAILED`，错误里不回显输入、没有堆栈（接口级）。读取：库里有已不支持的类型时，页面只把它显示成“此组件类型或配置不受支持，其他组件仍可使用。”，不渲染原始配置（浏览器）。Vitest 覆盖 Registry 的严格校验（T-0016） |
| 部分 Widget 失败不影响整页 | 通过 | 库里写入一条类型已不支持、配置不是对象、布局无法解析的组件后：首页聚合与组件列表仍然 200，其余组件的布局和内容不变（接口级）；页面上只有它显示错误卡片，其余 4 个组件正常，错误卡片紧接在其余组件下方（浏览器）。任务模块不可用时其余组件照常显示（A05）。pytest：provider 出错或超时只把该模块标为 unavailable |

验收中发现并修复的问题：布局无法解析的组件原先被服务端固定放在第 999 行，桌面网格被撑到 72,551 像素高，错误卡片在页面 7 万多像素以下。
现在改为紧接在其余组件下方（`services/api/app/domain/dashboards.py`，契约 ADR 第 5 节已更新），浏览器验收检查错误卡片的位置在 3,000 像素以内。

## 2. 验收场景（规范第 15 节）

全栈环境：Compose 6 个服务，经 Caddy 对外入口访问。两个脚本都**已纳入 CI**（`ci-infra.yml`）：
接口级 `sprint2_acceptance.py`（39 项）和浏览器 `sprint2_browser.cjs`（9 步，无头 Chromium，Playwright 1.63.0）。
两者都不要求空库：每次用新的邮箱，由 `tests/e2e/seed.py` 经 `docker compose exec api` 用应用自身的代码写入测试用户、工作空间与默认首页。

| 场景 | 期望 | 结果 |
|---|---|---|
| A03 viewer 修改 Widget | 返回 403 | 通过。接口：修改组件配置、添加、删除组件、保存布局、新建仪表盘均为 `403 FORBIDDEN`，数据未被改动；member 做同样的修改返回 200（确认 403 来自角色）。页面：viewer 切到别人的工作空间能看首页，没有编辑、添加、导入、设为首页入口 |
| A04 保存并刷新首页布局 | 布局一致，版本号更新 | 通过：见第 1 节 |
| A05 第三方服务断开 | 首页显示 stale/unavailable，其余模块可用 | 通过（本 Sprint 尚无 Connector）。接口：`modules.tasks` 为 `unavailable / not_configured`，汇总值为 `null`，`today_tasks` 为空。页面：两个任务组件显示“任务模块暂不可用，尚未接入任务服务。”，不显示 0；便签与快捷链接正常。真实的 Connector 断开在 Sprint 3 接入 Vikunja 后验证 |
| 版本冲突（S2-04） | 旧版本保存被拒绝，不覆盖别人的改动 | 通过。接口：另一个会话用旧版本保存布局 → `409 VERSION_CONFLICT`，`details.current_version` 为最新版本；旧版本修改组件同样 409；缺少组件的布局 → 422。页面：两个会话同时编辑，另一个会话先保存后，本页保存提示“已被其他人修改”并锁定保存，重新加载后显示对方保存的宽度 |
| 跨工作空间（沿用 S1-03） | 非成员 404 | 通过：非成员读取首页、修改组件，以及用自己的工作空间读取别人的仪表盘组件、删除别人的组件，均为 `404 NOT_FOUND` |
| 审计日志（契约 ADR 第 12 节） | 记录布局与组件变化，不记录内容 | 通过：`dashboard.updated`、`widget.created`、`widget.updated`、`widget.deleted` 均已记录，日志里不含便签文本 |

### 浏览器（S2-05，`tests/e2e/sprint2_browser.cjs`）

| 步骤 | 结果 |
|---|---|
| 首页显示 4 个入门组件；任务组件显示降级说明、不显示 0 | 通过 |
| 拖拽与调整大小 → 保存：后端布局与页面一致，版本 +1，移动端顺序不变 | 通过 |
| 刷新后布局不丢 | 通过 |
| 两个会话的版本冲突：提示、锁定、重新加载后显示最新布局 | 通过 |
| 添加组件（便签按文本显示）并在确认后删除 | 通过 |
| `Ctrl+K` 本地搜索能找到当前首页的组件；切换暗色主题 | 通过 |
| 库里有已损坏的组件：只有它显示错误卡片，其余组件正常，页面不被撑高 | 通过 |
| 390px 宽度下单列显示、无横向滚动（暗色主题） | 通过 |
| viewer 只读 | 通过 |

页面全程没有脚本错误（登录前查询当前用户的 401、版本冲突的 409 是预期响应）。本机连续运行 3 次均通过。

Sprint 1 的浏览器流程（`sprint1_browser.cjs`：首次安装 → 登录 → 修改设置 → 撤销会话 → 登出）在新的首页上同样通过，
也已纳入 CI：在重启检查之后清空数据卷、重新迁移，再从首次安装开始运行。

## 3. 任务清单（docs/TASKS.md Sprint 2）

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S2-01 Dashboard/Widget 数据表及读写 API | T-0014 契约（PR #20）、T-0015 后端（PR #21） | 迁移 0004（为已有工作空间补建默认首页）；8 个端点；`alembic check` 无差异；全栈脚本复验 |
| S2-02 Vue 顶栏、侧栏、响应式页面、dark/light theme | T-0016 前端（codex，PR #23） | 顶栏本地搜索（`Ctrl/Cmd+K`）与“创建”“通知”占位；移动端单列；暗色主题。浏览器验收复验 |
| S2-03 Widget registry/schema、基础卡片 | T-0014（服务端 Registry）、T-0016（前端 Registry 与 4 种卡片） | 服务端 422 与前端错误卡片见第 1 节 |
| S2-04 Grid 拖拽/布局保存/版本冲突处理 | T-0015、T-0016（GridStack 14.0.0） | 拖拽保存刷新与 409 见第 1、2 节；依赖核验见前端技术栈 ADR |
| S2-05 聚合接口、loading/error/stale、E2E | T-0015（聚合接口与模块降级）、T-0016（组件独立状态）、T-0017（全栈 E2E 纳入 CI，PR #22 与本 PR） | 接口级与浏览器验收均在 CI 运行 |

## 4. 交付完成定义（docs/DEVELOPMENT_SPEC.md 第 18 节）

| 条件 | 结果 | 说明 |
|---|---|---|
| 代码已提交到版本控制 | 通过 | PR #19（规划）、#20、#21、#22、#23 与本 PR |
| 所有必需依赖固定版本 | 通过 | `uv.lock`、`apps/web/package-lock.json`、`tests/e2e/package-lock.json`；新增的 GridStack 14.0.0 已在 npm registry 核验，记录在前端技术栈 ADR |
| 开发和部署命令可复现 | 通过 | `make env / up / migrate / smoke`；验收脚本在本机与 CI 共用 |
| 迁移可执行 | 通过 | 本机测试库、CI service container、Compose 容器内三处执行，均到 0004 |
| 自动测试通过 | 通过 | 后端 `96 passed`（含真实 PostgreSQL 16 / Redis 7 集成测试与强制竞态的并发测试）；前端 `Tests 82 passed`；CI：ci-api、Web CI（含生成类型一致性检查）、ci-infra（含接口级与浏览器全栈验收） |
| 手工 Smoke Test 通过 | 通过 | `make smoke`；浏览器流程见第 2 节 |
| 关键安全测试完成 | 通过（Sprint 2 范围） | viewer 写入 403、非成员 404、错误响应不回显输入、链接只允许 http(s) 且不带用户名和密码、便签只按文本显示（没有 `v-html`）、审计不含组件内容、写入 CSRF 与工作空间请求头 |
| README / 配置示例更新 | 通过 | `apps/web/README.md`（仪表盘与浏览器冒烟）、`docs/api-contract.md`、数据字典（迁移 0004）、契约 ADR |
| 未实现能力明确标注 | 通过 | “创建”“通知”为禁用的占位入口；全局搜索只搜本地导航与当前首页组件；任务组件在 Sprint 3 前显示“尚未接入” |
| 无明文密钥 | 通过 | 仓库只有占位符与测试常量；`.env` 被 git 忽略 |
| 无未经验证的第三方 API 假设 | 通过 | Sprint 2 不连接第三方服务；新增库的版本在 npm registry 核验 |

## 5. 未运行 / 限制

| 项目 | 原因 |
|---|---|
| 真实 Connector 断开（A05 的完整形态） | 本 Sprint 没有 Connector；Sprint 3 接入 Vikunja 后验证 stale / unavailable 的切换 |
| 布局导入 / 导出的全栈浏览器验证 | 由前端 Vitest（导入前严格校验、导入总是新建仪表盘、导出不含 ID 与版本）与后端 pytest（导入任一组件不合法则整体 422、什么都不创建）覆盖，未放进浏览器脚本 |
| 触屏拖拽、真实移动设备与读屏 | 只有桌面无头浏览器的鼠标拖拽、390px 视口检查，以及键盘输入布局的 Vitest |
| GridStack 官方站点与 GitHub 发布页 | 验收环境的网络策略拒绝访问（403），以 npm registry 元数据与包内类型为准 |
| 本地 `docker build` 的网络 | 与 Sprint 1 相同：本地构建时额外传入代理与 CA（只在构建阶段）；仓库 Dockerfile 未改，CI 按原样构建 |

## 6. 遗留问题与建议（不影响 Sprint 2 验收）

1. **添加组件的行号上限**（PR #23 审查的非阻塞意见）：新组件放在 `y = 现有组件的最大 y + h`，没有限制在 999 以内。用户把某个组件的行号设为 999 后，“添加组件”会一直提示配置无效。建议下一次前端任务改成 `Math.min(bottom, 999)`。
2. **存在无法识别的组件时不能导出**：前端拒绝导出包含未知类型的仪表盘，需要先删除该组件。这是有意的保守行为，记录在这里供以后决定。
3. **GitHub 上 `@codex` 派活**：仍通过 Codex 应用的云端对话派发任务（与 Sprint 1 相同）。

## 7. 验收时执行的命令（摘录）

```
# 全新数据卷的独立 Compose 项目，按 CI 的顺序运行；端口 18081
$ COMPOSE_PROJECT_NAME=ws-browser HTTP_PORT=18081 CORS_ALLOWED_ORIGINS=http://127.0.0.1:18081 docker compose up -d --wait
$ make migrate && make smoke
Running upgrade  -> 0001 / … / 0003 -> 0004
冒烟检查全部通过
$ python3 tests/e2e/sprint1_acceptance.py --base-url http://127.0.0.1:18081
全部通过
$ python3 tests/e2e/sprint2_acceptance.py --base-url http://127.0.0.1:18081
全部通过
$ node tests/e2e/sprint2_browser.cjs http://127.0.0.1:18081
通过  拖拽与调整大小 → 保存：后端保存的布局与页面一致，版本 1 → 2，移动端顺序不变
通过  DoD：刷新后布局不丢（A04 页面级）
通过  版本冲突：另一个会话先保存后，本页保存提示“已被其他人修改”并锁定；重新加载后显示最新布局，不覆盖对方
通过  DoD：库里有已损坏的组件时只有它显示错误卡片，其余组件正常，页面不被撑高
…（共 9 步）
全部通过（页面无错误）
$ make down && docker compose up -d --wait && docker compose exec -T api alembic current   # 重启后数据卷保留
0004 (head)
$ docker compose down -v && docker compose up -d --wait && make migrate                  # 清空后从首次安装开始
$ node tests/e2e/sprint1_browser.cjs http://127.0.0.1:18081
全部通过（页面无错误）

$ cd services/api && uv run ruff check . && uv run mypy app && uv run pytest -q   # 真实 PostgreSQL / Redis
96 passed
$ cd apps/web && npm ci && npm run lint && npm run typecheck && npm run test && npm run build && npm run check:api
Tests  82 passed (82)
```
