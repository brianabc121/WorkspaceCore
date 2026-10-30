# Sprint 3 验收报告（进行中）

- 验收人：claude（协作任务 T-0023，按 D-0003 负责每个 Sprint 的联调与验收）
- 日期：2026-10-30
- 状态：**接口级验收已完成并纳入 CI**；人类在对话中同意先做不依赖前端的部分。
  浏览器部分（连接 Vikunja 的页面、任务视图、原生改动在页面上的更新）等前端 T-0022（codex）合并后补充，届时定稿本报告并勾选 `docs/TASKS.md`。
- 是否进入 Sprint 4：等本报告定稿后由人类决定（D-0003 / D-0004）。

## 1. Sprint 3 DoD（docs/DEVELOPMENT_SPEC.md 第 13 节）

全栈环境：Compose 6 个服务 + `infra/vikunja/compose.yaml` 启动的 Vikunja 2.7.1（锁定 digest），工作台经 integrations 网络访问
`http://vikunja:3456`，Vikunja 的 Webhook 投递到 `http://caddy:8080`。脚本 `tests/e2e/sprint3_acceptance.py`（29 项）已纳入 CI（`ci-infra.yml`）。

| 条件 | 结果 | 证据 |
|---|---|---|
| 外部原生任务改动最终出现在工作台 | 通过（接口级） | 用 Vikunja 用户自己的会话直接修改、新建、删除任务（不经过工作台）→ 经自动注册的 Webhook，工作台的任务列表在 30 秒内更新（本机实测约 1 秒），删除的任务记 tombstone 后不再显示。页面上的显示等 T-0022 |
| 重复提交不创建双份任务 | 通过 | A06：同一个 `Idempotency-Key` 提交两次，第二次原样返回并带 `Idempotent-Replayed: true`，Vikunja 项目里只有一个同名任务 |
| 模拟 webhook 丢失可经对账补齐 | 通过 | A09：停掉 caddy 后在 Vikunja 里修改任务，Inbox 没有收到事件（Webhook 被 Vikunja 丢弃）；恢复 caddy 后不做手动同步，Worker 的周期对账（CI 中 `RECONCILE_INTERVAL_MINUTES=1`）把修改补到投影 |

## 2. 验收场景（规范第 15 节）

| 场景 | 期望 | 结果 |
|---|---|---|
| A05 第三方服务断开 | 首页显示 stale/unavailable，其余模块可用 | 通过。停掉 Vikunja 后：创建任务 `503 EXTERNAL_UNAVAILABLE`；任务列表仍从本地投影读出，`sync.status = stale`（`reason: source_unavailable`）；首页任务模块 `stale`，汇总仍显示，4 个组件照常返回。恢复后手动同步 `202`，任务模块回到 `ok` |
| A06 创建任务重放同一幂等键 | 最多创建一个外部任务 | 通过（见第 1 节） |
| A07 相同幂等键不同请求体 | 返回 409 | 通过：`409 IDEMPOTENCY_KEY_REUSED`，被拒绝的请求没有到达 Vikunja |
| A08 Vikunja Webhook 重复送达 | 事件最多执行一次有效副作用 | 通过：用该集成的密钥签名的同一个事件投递 3 次都返回 `202`，Inbox 只有一条，状态 `completed`、`attempts = 1`；签名与请求体不匹配返回 `401 WEBHOOK_SIGNATURE_INVALID` |
| A09 Webhook 丢失 | 周期对账最终纠正投影 | 通过（见第 1 节） |

其余检查（同一脚本）：

| 检查 | 结果 |
|---|---|
| 保存集成 `201`，响应不含 Token；测试连接逐项通过（可达、版本 2.7.1、Token、任务读权限、Webhook 权限） | 通过 |
| 测试通过后的首次同步：项目与已有任务出现在工作台 | 通过 |
| Worker 自动为项目注册 Webhook（目标为 integrations 网络里的 caddy，事件 5 种） | 通过 |
| 经工作台修改任务 `200`，Vikunja 同步变化；旧 `version` 修改 `409 VERSION_CONFLICT`；删除 `204`，Vikunja 里已不存在 | 通过 |
| 审计日志记录集成与任务的写操作，不含 Token 与任务标题 | 通过 |

验收中发现并修复的问题：Vikunja 断开后，最近一次成功同步仍在 15 分钟内时首页任务模块显示 `ok`，与 A05 不符。
现在集成处于 `unavailable`（最近一次访问 Vikunja 失败）时显示 `stale`（`reason: source_unavailable`），数据照常显示，
下一次成功访问后回到 `ok`（`services/api/app/domain/connectors.py`，契约 ADR 第 10 节已补充）。

Sprint 1、Sprint 2 的接口级验收在同一个环境里同样通过（`sprint1_acceptance.py`、`sprint2_acceptance.py`）。

## 3. 未运行（等 T-0022）

| 项目 | 原因 |
|---|---|
| 浏览器 E2E：连接 Vikunja → 新建 / 编辑任务 → 在 Vikunja 原生修改 → 工作台页面显示更新 | 前端 T-0022（codex）尚未合并 |
| `docs/TASKS.md` 勾选 S3-01 ~ S3-06 | 按约定由本任务在全部验收通过后统一勾选 |

## 4. 本机运行方式

```bash
make up && make migrate
make vikunja-up
python3 tests/e2e/sprint3_acceptance.py
```

脚本会临时停掉 caddy（约 10 秒）与 vikunja（约 20 秒）来模拟 Webhook 丢失与服务断开。默认的对账间隔是 5 分钟，
本机想更快看到 A09 的结果，可以在 `.env` 里设 `RECONCILE_INTERVAL_MINUTES=1` 后重新 `make up`。
