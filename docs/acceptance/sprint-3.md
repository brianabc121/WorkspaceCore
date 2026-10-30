# Sprint 3 验收报告

- 验收人：claude（协作任务 T-0023，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30
- 结论：**Sprint 3 的 DoD 与 A05 ~ A09 全部通过**，接口级与浏览器验收都已纳入 CI；“未运行 / 限制”逐条见第 5 节。
- 是否进入 Sprint 4：等待人类批准（D-0003 / D-0004：进入下一个 Sprint 由人类决定）。

全栈环境：Compose 6 个服务 + `infra/vikunja/compose.yaml` 启动的 Vikunja 2.7.1（锁定 digest），工作台经 integrations 网络访问
`http://vikunja:3456`，Vikunja 的 Webhook 投递到 `http://caddy:8080`。两个脚本都**已纳入 CI**（`ci-infra.yml`）：
接口级 `tests/e2e/sprint3_acceptance.py`（29 项）和浏览器 `tests/e2e/sprint3_browser.cjs`（10 步，无头 Chromium，Playwright 1.63.0）。
两者都不要求空库：每次新建工作台用户（`tests/e2e/seed.py`）、Vikunja 用户、受限 Token 与项目。CI 中 `RECONCILE_INTERVAL_MINUTES=1`。

## 1. Sprint 3 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

| 条件 | 结果 | 证据 |
|---|---|---|
| 外部原生任务改动最终出现在工作台 | 通过 | 接口级：用 Vikunja 用户自己的会话直接修改、新建、删除任务（不经过工作台），经 Worker 自动注册的 Webhook，工作台任务列表在 30 秒内更新（本机实测约 1 秒），删除的任务记 tombstone 后不再显示。浏览器：在 Vikunja 里改标题后，任务页点“刷新任务”即显示新标题；首页“待办汇总”显示真实数字 |
| 重复提交不创建双份任务 | 通过 | A06：同一个 `Idempotency-Key` 提交两次，第二次原样返回并带 `Idempotent-Replayed: true`，Vikunja 项目里只有一个同名任务。浏览器：新建抽屉提交一次，请求带 UUID 格式的 `Idempotency-Key`，Vikunja 里恰好一个。前端的“结果未知 → 同键查询”由 T-0022 的 Vitest 与 mock 浏览器脚本覆盖 |
| 模拟 webhook 丢失可经对账补齐 | 通过 | A09：停掉 caddy 后在 Vikunja 里修改任务，Inbox 没有收到事件（Webhook 被 Vikunja 丢弃）；恢复 caddy 后不做手动同步，Worker 的周期对账把修改补到投影 |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A05 第三方服务断开 | 首页显示 stale/unavailable，其余模块可用 | 通过。停掉 Vikunja 后：创建任务 `503 EXTERNAL_UNAVAILABLE`；任务列表仍从本地投影读出，`sync.status = stale`（`reason: source_unavailable`）；首页任务模块 `stale`，汇总仍显示，4 个组件照常返回。恢复后手动同步 `202`，任务模块回到 `ok`。页面上 `stale` 显示“数据可能已过期”与时间（T-0022 组件状态） |
| A06 创建任务重放同一幂等键 | 最多创建一个外部任务 | 通过（见第 1 节） |
| A07 相同幂等键不同请求体 | 返回 409 | 通过：`409 IDEMPOTENCY_KEY_REUSED`，被拒绝的请求没有到达 Vikunja |
| A08 Vikunja Webhook 重复送达 | 事件最多执行一次有效副作用 | 通过：用该集成的密钥签名的同一个事件投递 3 次都返回 `202`，Inbox 只有一条，状态 `completed`、`attempts = 1`；签名与请求体不匹配返回 `401 WEBHOOK_SIGNATURE_INVALID` |
| A09 Webhook 丢失 | 周期对账最终纠正投影 | 通过（见第 1 节） |

接口级的其余检查：保存集成 `201` 且响应不含 Token；测试连接逐项通过；首次同步带来项目与已有任务；Webhook 自动注册（事件 5 种）；
经工作台修改 `200`、旧 `version` `409 VERSION_CONFLICT`、删除 `204`；审计日志记录集成与任务的写操作且不含 Token 与任务标题。全部通过。

### 浏览器（`tests/e2e/sprint3_browser.cjs`）

| 步骤 | 结果 |
|---|---|
| 设置页新建连接（服务地址 `http://vikunja:3456`）→ 保存 → 测试连接：必需检查通过、状态“已连接”，页面始终不出现 Token | 通过 |
| 任务页显示首次同步的任务，标明 Vikunja 来源，“在 Vikunja 中打开”指向原生界面 | 通过 |
| 新建任务抽屉：带 `Idempotency-Key` 提交一次，Vikunja 里只有一个，列表随即显示 | 通过 |
| 编辑抽屉：改标题并标为完成，Vikunja 同步变化 | 通过 |
| 在 Vikunja 原生修改任务，工作台任务页显示更新 | 通过 |
| 看板按“未完成 / 已完成”分列 | 通过 |
| 首页“待办汇总”显示真实数字 | 通过 |
| 删除任务（确认后）：工作台与 Vikunja 里都已删除 | 通过 |
| 390px 宽度下任务页无横向滚动 | 通过 |
| viewer：能看任务，没有新建、保存、删除入口 | 通过 |

页面全程没有脚本错误。本机连续运行 3 次均通过。Sprint 1、Sprint 2 的接口级与浏览器验收、重启检查在同一个 CI 流程里同样通过。

验收中发现并修复的问题：Vikunja 断开后，最近一次成功同步仍在 15 分钟内时首页任务模块显示 `ok`，与 A05 不符。
现在集成处于 `unavailable`（最近一次访问 Vikunja 失败）时显示 `stale`（`reason: source_unavailable`）（PR #32，契约 ADR 第 10 节已补充）。

## 3. 任务清单（docs/TASKS.md Sprint 3）

| 条目 | 完成方式 | 证据 |
|---|---|---|
| S3-01 核验锁定版本 OpenAPI、认证/字段映射并形成 ADR | T-0018（PR #27）；T-0020 补充第 5.5 节 | `docs/decisions/2026-10-30-vikunja-2.7.1-verification.md`：镜像 digest、运行实例导出的 OpenAPI（存档并做契约测试）、API Token 与最小权限、字段映射、Webhook 签名与投递；`test_vikunja_live.py` 对真实实例复核 |
| S3-02 Integration、Token 授权及 Connector 基类 | T-0019 契约（PR #29）、T-0020 后端（PR #30）、T-0022 前端（codex，PR #33） | 迁移 0005；Token 加密保存、只写不读；SSRF 允许列表；Connector 能力分层与统一错误分类；设置页连接、测试、同步（浏览器验收） |
| S3-03 Vikunja project/task list/create/update/detail API 与 UI | T-0020、T-0022 | 读本地投影、写经 Connector；任务页列表 / 看板 / 抽屉（浏览器验收） |
| S3-04 Idempotency-Key 和未知执行状态处理 | T-0020、T-0022 | A06 / A07；后端 pytest 覆盖结果未知、确认未生效、仍在处理、确定没发出；前端 Vitest 覆盖同键查询与换键 |
| S3-05 Webhook 签名校验、Inbox、Worker、重试、死信 | T-0021（PR #31） | 迁移 0006；A08；pytest 覆盖重复、乱序、签名错误、超大 body、重试与死信、崩溃后重新领取、`SKIP LOCKED` |
| S3-06 周期对账、tombstones、同步 cursor、契约/E2E 测试 | T-0021、T-0023（PR #32 与本 PR） | A09；增量 + 定期全量；删除记 tombstone；游标失败不前移；归档 OpenAPI 的契约测试；本报告的全栈与浏览器 E2E |

另外：T-0024（codex，PR #28）修复了 Sprint 2 遗留的“第 999 行附近无法新增组件”。

## 4. 实际执行的命令

```bash
make up && make migrate
python3 tests/e2e/sprint1_acceptance.py      # 全部通过
python3 tests/e2e/sprint2_acceptance.py      # 全部通过
make vikunja-up
python3 tests/e2e/sprint3_acceptance.py      # 29 项全部通过
node tests/e2e/sprint2_browser.cjs           # 全部通过（页面无错误）
node tests/e2e/sprint3_browser.cjs           # 10 步全部通过（页面无错误），连续 3 次
make vikunja-down && make down && docker compose up -d --wait   # 重启后 alembic current 仍为 head
docker compose down -v && docker compose up -d --wait && make migrate
node tests/e2e/sprint1_browser.cjs           # 全部通过（页面无错误）
```

后端：`cd services/api && uv run pytest -q` → 231 passed, 4 skipped（4 个真实实例测试需要 `TEST_VIKUNJA_*`）。
前端：`cd apps/web && npm run lint && npm run typecheck && npm test && npm run check:api && npm run build` → 108 passed，其余通过。

## 5. 未运行 / 限制

| 项目 | 说明 |
|---|---|
| 同一秒内的并发修改 | Vikunja 只按秒保存 `updated`，同一秒内别人的修改发现不了冲突（核验 ADR 第 5.5 节，契约第 6.3 节明确接受） |
| Vikunja 使用 PostgreSQL / MySQL | 只验证了 Compose 中的 sqlite；时间精度处理（截到秒）对其他后端同样成立，但未实测 |
| 页面刷新后的“结果未知”提示 | 未确认的操作保存在页面内存里，硬刷新后丢失；界面提示先核对列表（T-0022 的设计取舍）。后端的幂等记录保留 24 小时，用同一个键重放仍然安全 |
| 死信查看界面 | 目前只能用 SQL 查看（部署文档第 8 节）；界面展示属于后续 Sprint 的通知 / 运维范围 |
| Webhook 端点限流 | 没有单独限流，签名校验成本低，Caddy 另有全局请求体上限 |
| 标签写入、负责人、评论、附件 | 不在 Sprint 3 范围（契约第 6.3 节），在 Vikunja 原生界面操作 |
| PR #33 的非阻塞审查建议 | 测试结果显示错误码、显示已保存的服务地址、`public_url` 恢复为默认；不影响 DoD，可在后续任务处理 |
