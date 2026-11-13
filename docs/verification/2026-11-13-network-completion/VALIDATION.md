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
