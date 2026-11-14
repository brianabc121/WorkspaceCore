# T-0224：不支持的 Kubernetes 操作在写入前拒绝

本轮依据已批准计划 Task 5 的 fail-closed 要求推进有限运行档位保护，**没有实现完整 exec、文件、快照适配**，没有修改两台服务器或生产网络开关。继续沿原 PR203，不重复 PR197 的开发和历史实机测试。

## 问题与行为

原控制器只接受 create/start/stop/delete，但领域层仍能把其他操作排队：执行可能先启动/重建环境，快照可能先排队停止，模板可能先写成 building，恢复可能先改 generation/compute reservation。等到控制器或 payload 校验拒绝时，已经发生了不必要的状态写入。文件请求还会被当作超时处理。

现在入口检查与命令队列共用控制器的有限操作集合，返回已有 `RUNTIME_PROFILE_UNSUPPORTED`。作用于 Kubernetes 环境和宿主，Docker 与已支持的四种生命周期命令继续可用。初始化、文件、执行、快照、归档、模板构建在相应写入之前拒绝。管理员模板重建将该领域错误转换为正式 422 响应。

恢复操作先只读构造/验证材料，再进入准入；准入可能申请配额审批或停止其他环境，因此不能等准入完成才拒绝。准入刷新并锁住环境后再读取/验证材料，避免采用先前快照。支持的无快照重建及 Docker 原有恢复路径保留。

后台回收捕获此明确错误并保存原因，不把它计作完成归档/删除；继续处理其他 Docker 环境。需要快照的删除仍保留待删除请求和数据，不自动改成无快照删除。Kubernetes 镜像清理由 kubelet 负责，跳过 Docker prune 指令。已有历史队列记录没有被清理或改写，控制器仍拒绝不支持的记录。

## 实际验证

本地使用独立 PostgreSQL/Redis 测试容器，只绑定执行器 loopback 55432/56379；连接配置在未跟踪的 0600 缓存文件中，连接凭据不提交 Git。测试使用 `app_env=test`；合成的已验证宿主/配额夹具不代表生产准入或实机遥测。没有数据库结构变化、生产迁移或 ACL/注册密钥操作。

```bash
cd services/api
# 安全地从本地测试配置载入 TEST_DATABASE_URL / TEST_REDIS_URL；不打印变量。
set -a
source /workspace/.cache/t0224-tools/local-tests.env
set +a
.venv/bin/pytest -q -rs tests/runtime_v2 tests/test_runner_reclaim.py \
  tests/test_runner_command_order.py tests/test_runner_api.py --tb=short
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app migrations tests scripts
.venv/bin/python -m scripts.export_openapi --check
```

最终结果为 **277 passed, 1 skipped in 161.64s**，含新增38项准入测试。跳过项是已有的官方 kubectl TLS 测试，本轮没有设置经摘要核验的 `RUNTIME_V2_TEST_KUBECTL`，不能把它算通过。Ruff/format566文件、strict mypy562源文件、OpenAPI检查通过。结果由同目录 `operation-admission-results.txt` 记录。断言不依赖请求结束回滚：捕获拒绝后主动提交，再检查命令/运行/快照表以及环境/模板状态。后台测试运行真实 tick 并提交，验证另一个 Docker 环境仍被正常归档。

初次有效 RED 为 `18 failed, 11 passed in 30.79s`；主机级命令/镜像回收扩展为 `3 failed, 30 deselected in 3.15s`。只读审查发现模板和恢复前置检查两处 Important，均已修复并复审通过。两个模板用例一度因夹具缺少可信工作集指标而提前失败，这不是产品缺陷证据；补齐夹具后重新执行原方法，真实确认新建模板遗留记录、旧模板变为 building/version2、恢复变为 creating/generation2、五环境时先产生配额审批。重放命令：

```bash
# 只在本次 pytest 进程内替换两段原方法；不修改源文件、索引或服务器。
PYTHONPATH="$PWD:../../docs/verification/2026-11-14-runtime-network-followup" \
  .venv/bin/pytest -p replay_original_operation_paths -q \
  tests/runtime_v2/test_operation_admission.py \
  -k 'template_build or restore_rejects' --tb=short
```

使用随文重放模块实际 `4 failed, 34 deselected in 11.39s`。管理员重建原先未转换的 RunnerError 另已通过实际 HTTP 请求复现。一次误指定不存在的测试文件导致 `exit 4 / no tests ran`，已修正命令，不计作测试运行结果。此前中间实现的 `272 passed, 1 skipped /157.01s` 也不替代最终版本验证。

## 当前实机与剩余门槛

2026-11-14 **04:08 UTC（北京时间12:08）**：平台 TCP 代理到 A/B 的22端口均约5秒返回 HTTP503 `connection timeout`。没有取得 SSH 公钥/进入认证，没有执行远端命令。平台运行状态/源配置版本没有变化；不能凭用户“可以了”或平台 Running 将新 Tagged Reusable 密钥的实际应用标为已验证。本轮没有运行 Tailscale 客户端注册命令、改标签或保存密钥。

完整运行适配、真实公网 LB 与两个外部来源、生产存储配额和满负载容量仍待完成。本轮单元/数据库回归不能替代两节点数据面验收。生产 `RUNNER_NETWORK_VERIFIED=false`、T-0224 in_progress、PR203 草稿继续保留；最新完整 CI 的状态须以本轮提交对应工作流为准。
