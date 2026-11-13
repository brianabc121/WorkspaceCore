# T-0224 网络执行链交付验证

实现基线：origin/main 43b5bfa7。网络源码提交：e80ccc72。交付 PR：<https://github.com/brianabc121/WorkspaceCore/pull/197>。

本轮完成同实例受管私网、授权 TCP/TLS 网关与撤销断连、网络管理接口及页面的代码交付。人类已取消本轮 SSH 登录和远端测试；没有认证或修改其服务器。真实 Cilium/LB/双物理节点验收没有执行，不能把下面的本地/CI结果视为已部署或跨机已验证。部署方仍需执行 `infra/runtime-v2/acceptance_two_nodes.py` 和文档中的前置验收，之后才能开启 `RUNNER_NETWORK_VERIFIED`。

## 当前源码实际通过的检查

### 本地网络组合

工具链：Python3.12、PostgreSQL16.15、Redis7.4.12、官方kubectl v1.35.10（SHA256核验）。数据库与Redis仅用于本地测试。

```text
TEST_DATABASE_URL=<local PostgreSQL> TEST_REDIS_URL=<local Redis>
REQUIRE_INTEGRATION=1 RUNTIME_V2_TEST_KUBECTL=/tmp/t0224-tools/kubectl
pytest -q tests/runtime_v2 tests/test_runner_network_gateway_control.py
          tests/test_runner_gateway_migration.py tests/test_runner_network.py

236 passed in 179.41s (0:02:59)
exit_code: 0
```

范围包括：真实socket/TLS转发与撤销；共享Kubernetes序列化收敛；Pod/Deployment/PVC代次绑定；可信节点容量与调度；真实数据库的权限、版本、过期、容量与控制面；官方kubectl对本地TLS接口的边界测试。后者不是运行了一个真实Kubernetes集群。

```text
alembic upgrade head   -> exit 0
alembic downgrade base -> exit 0
alembic upgrade head   -> exit 0
```

迁移在独立空库实际执行完整链，结束后删除该测试库；没有生产迁移。

### 完整前端与代码一致性

本地Node22.23.4使用锁文件 `npm ci` 安装457包，lint/typecheck退出0。完整前端以相同源码的CI运行作为完整证据：

<https://github.com/brianabc121/WorkspaceCore/actions/runs/43397249171>

```text
npm run check:api -> success
npm run lint      -> success
npm run typecheck -> success
npm run test:
Test Files 79 passed (79)
Tests      794 passed (794)
npm run build:
✓ built in 10.14s
job conclusion: success
```

OpenAPI通过正式导出器生成，TypeScript通过openapi-typescript7.13.0生成。后端CI已通过Ruff、strict mypy和OpenAPI一致性；完整pytest与全栈验收的最终结果以PR中最新CI记录和说明为准，不用局部236项替代全套通过声明。

### 网关镜像

新增 `Runtime network image` CI，从锁定的API构建文件生成测试用基础镜像，再构建网关；验证非root用户10001:10001、kubectl版本，以及未配置时默认启动以exit2拒绝。该工作流只构建和检查测试镜像，不发布镜像，不连接用户服务器，不开启生产网络。实际构建结果以该CI检查为准。

## 修复的真实失败

- 独立审阅5项问题：Kubernetes省略空列表导致策略不收敛；单条声明影响共享网关；平台网关Pod导致遥测失败；过期冒充断连确认；停止环境仍显示连接地址。均补回归并纳入上述236项。
- 单个目标失效不应阻止其他已批准通信权限生效：新增测试实际RED 1 failed，修复后的回归纳入236项。
- 撤销/过期且尚未确认撤下的入口不能过早释放共享容量：参数化回归实际RED 2 failed / 1 passed，修复后容量与inactive ACK组合4 passed，随后纳入236项。
- 公网LB等待只撤下公网绑定，私网继续发布；inactive ACK不能把有效记录错误标为监听已启动。
- 部署接线遗漏：真实Docker Compose渲染结果原本没有网关令牌、地址和网段配置，默认/显式配置两种回归均RED。补齐共享环境与`.env.example`后，`pytest -q tests/test_runner_network_compose.py` 实际 `2 passed in 0.55s`；覆盖API与Worker的渲染环境及实际Settings读取。
- 首轮完整API CI报告失败；本地实际复现 `test_validation_errors_are_documented_with_unified_format` 与 `test_every_write_requires_a_csrf_token`，2 failed/39.52s。根因是机器网关误进入浏览器OpenAPI，带入默认HTTPValidationError并被当成浏览器CSRF写操作。加强 `test_v2_internal_and_gateway_routes_are_not_documented` 后另观察到1 failed/6.30s；修复只把内部路由标为include_in_schema=False，未调整CSRF矩阵。重新导出OpenAPI/类型后，两个契约、完整安全矩阵和真实网关鉴权组合 `8 passed in 473.35s`。接口生成一致性/前端typecheck、Ruff548文件/strict mypy544文件/OpenAPI检查同时通过。完整CI以修复后最新提交为准。

## 未通过或中断的本地运行记录

恢复环境后，一次网关组合中 `test_kubectl_subprocess_is_bounded_and_does_not_cache_identity[fresh-identity]` 触发0.2秒超时。后续没有修改该超时，在安静环境的上述236项组合中通过。

本地第一次前端全套与编译/类型检查并行，观察到以下model-connections用例失败，随后中断：

- `test_connections_list_filters_total_and_summary`
- `test_official_relay_and_multi_model_form`
- `test_saved_key_is_never_revealed_or_submitted_blank`
- `test_statuses_do_not_collapse_to_empty`
- `test_draft_and_compat_credentials`
- `test_workspace_switch_and_stale_responses`

另一次本地串行前端全套观察到以下失败，完整摘要输出前停止重复运行；没有计为本地全套通过：

- im: `creates a group with an Idempotency-Key and opens it`
- approval-center-redesign: `explains the rules read-only, without batch approval or permanent trust`
- kb-pages: `is a standalone page with its own rail, space groups and graph`
- kb-pages: `colors the graph by type group by default, lists the types in the legend and filters by a single type`
- kb-pages: `shows a note with resolved links, backlinks, usage and moves it to the trash with its version`

CI完整794项通过是单独取得的证据。没有通过增加测试超时、跳过用例或削弱断言取得该结果。

## 交付边界

跨实例/公网仅支持TCP；同实例私网支持TCP/UDP。IPv6和任意公网出口不在当前安全档位内。当前Kubernetes执行器对exec、文件、快照等未实现操作明确拒绝；旧Docker环境保留原后端。这些更广的T-0224事项未冒充完成，任务继续in_progress；本PR由Claude审查，Codex不合并。

## Claude 接手修复 PR #197 审查问题（2026-11-13）

人类 2026-11-13 要求 Claude 直接修好 PR #197 的审查问题并合并，不再等 Codex。Claude 没有推送 Codex 的分支：在自己的分支上并入 `codex/T-0224-network-completion`（head `4679f7a`）后修改，经新的 PR 合并。

| # | 问题 | 修复 | 回归测试（修复前实际失败 → 修复后通过） |
|---|---|---|---|
| 1 | 网关回执被拒（owner 在 `/config` 之后改了规则）时，`reconcile` 外层 `except BaseException` 关掉所有工作空间的监听和会话 | `Gateway._ack` 返回是否被接受；被拒只撤下这一条绑定的监听，下一轮按最新声明重开 | `tests/runtime_v2/test_gateway.py::test_rejected_ack_withdraws_only_that_binding` |
| 2 | 每新增一条入站规则或公网入口都把服务改回 `approved_pending_apply`，`NetworkGateway.target()` 因此暂时撤下同一服务已生效的兄弟规则并断开会话 | 只有服务的第一个网关子记录（`desired_policy` 的 `gateway_ingress` 由关变开）才提升服务版本；后续子记录沿用已生效的节点策略 | `tests/test_runner_network_gateway_control.py::test_second_gateway_child_keeps_live_sibling_and_service` |
| 3 | 节点上有一个已停止的环境（Deployment 副本数 0、保留 PVC、没有 Pod）时 `seen != set(own)`，节点永远发不出资源上报 | 副本数为 0 的环境按保留卷计入：kubelet 没有 Pod 就没有卷用量，取绑定的本地 PV 声明容量（上限，不是 0）；副本数 1 却没有 Pod 仍拒绝上报 | `tests/runtime_v2/test_node_telemetry.py::test_stopped_environment_reports_retained_volume_capacity`，原 `stopped-pvc` 用例改为 `pod-missing` 与 `stopped-pvc-unbound` 两个拒绝用例 |
| 4 | `desired_policy` 去掉了行锁却仍改 `desired_generation` / `desired_revision` / `state`，可能与 `update_rule` 并发写出两份共用同一版本的配置 | 扫描仍不加锁（轮询不等 owner 写操作）；要改的那一行先 `FOR UPDATE` 并重读（`populate_existing`），条件仍成立才写 | 并发时序难以稳定复现，未单独加测试；由完整后端测试覆盖原有行为 |
| 5 | 页面不认识 `NETWORK_SOURCE_FORBIDDEN`、`NETWORK_GATEWAY_CAPACITY`、`NETWORK_GATEWAY_PORTS_EXHAUSTED`，只显示“请求失败，请稍后重试” | `ContainerNetworkPanel.vue` 补三条说明（仍不显示服务器原文，避免回显密码） | `apps/web/tests/container-network-management.test.ts` 参数化用例加三种错误码 |
| 7 | 每 30 秒的后台刷新会让 `fresh` 变成 false，清空正在输入的确认密码 | 只在读取失败或没有数据时清空密码 | `container-network-management.test.ts::keeps the typed password across a successful background refresh` |

审查里的 6、8、9、10（续租间隔、每次轮询取新的策略版本、`apply_error` 清空、控制器 2 秒周期）是性能或行为取舍，没有确认为缺陷，本次不改，留给 T-0224 后续核实。

本地实际结果（本会话自己的 PostgreSQL 16 与 Redis 容器）：

- `ruff check .` 通过，`ruff format --check .` 548 个文件已格式化，`mypy app tests` 475 个文件无问题；
- 后端全量 `TEST_DATABASE_URL=… TEST_REDIS_URL=… REQUIRE_INTEGRATION=1 pytest -q`：`1732 passed, 16 skipped in 3284.21s`；
- 前端 `eslint . --max-warnings 0`、`vue-tsc --noEmit` 通过，`vitest run` 79 个文件 798 项通过，`vite build` 成功，`npm run check:api` 后 `schema.d.ts` 无变化。

真实两节点 / Cilium / 负载均衡的数据面验证仍未做，`RUNNER_NETWORK_VERIFIED` 仍默认关闭，与上文一致。
