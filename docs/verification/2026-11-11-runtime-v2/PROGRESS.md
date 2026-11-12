# T-0224 实施进度（未完成）

工作分支：codex/T-0222-runtime-v2-placement-network；当前检出 /workspace/WorkspaceCore-runtime-v2 是持久实体目录。原 /workspace/WorkspaceCore 未修改。基线 a1053c7。用户明确要求全面实施；不需要再次批准开发，但不能把尚未实现的部分标成完成。

## 合入主干后的当前状态

2026-11-12 09:20 UTC 合入 origin/main 36c6fb5e（存储设置与 IM 修复）。主干先占用原编号，当前任务为 **T-0224**；PR193/head 分支沿用，任务 owner 仍 codex、in_progress。当前唯一迁移 head 为 **0065**；主干 0060 不改，本任务原 0060–0064 顺延为 0061–0065。下列早期测试数量是合入前证据，正在重新复验集成，不能视为新基线通过。

## 合入前检查点（2026-11-12）

PR #193 仍为草稿、不可合并；T-0222 保持 in_progress。下文早期记录是历史证据，当前结果如下：

- 物理身份防重：0064 全局唯一账本、两种 SSH 安装前置占位和 runner 只读 machine-id 挂载已实现。真实 PostgreSQL 证明同机跨池/跨 workspace 争用只能成功一次，不自动转移身份。报告身份被拒绝时不下发 stray 清理。迁移降级先锁账本，再拒绝删除非空登记。
- 服务器删除与 SSH 作业启动串行化；运行中的 SSH 安装/卸载阻止删除，避免远程变更尚在执行时释放身份。两项回归先 RED 后 GREEN，完整 SSH 文件 7 passed；独立复审正在进行。
- 最新完整 API 非 integration：**642 passed, 42 skipped, 795 deselected in 176.01s**。最新完整前端：**77 files / 763 tests passed in 428.07s**。这是物理身份基础实现之后、下述心跳/磁盘及后续控制器/UI修改之前的检查点，不是最终提交全量验收。
- 相关真实 PG 组合 **56 passed in 231.38s**；跨 workspace 争用和 OpenAPI **6 passed in 14.25s**；降级并发 **2 passed in 9.55s**。严格 mypy **293 source files** 成功，Ruff 通过。Node 22.23.4 的 lint/typecheck/check:api/build 均 exit 0，原有 chunk / PURE 警告保留。
- 控制器协议补充：命令强制绑定实际 host_id；未知资源心跳允许领取已有指令，保留旧显示读数并撤销容量新鲜度，完整 poll 本身不能恢复准入。9 项单元回归 RED 后 GREEN，含真实 PG 指令投递合计 **10 passed in 3.31s**。
- 磁盘准入保守保留所有保有环境的预算，并给宿主保留 10% 总盘余量。没有可信逐卷用量时不抵扣旧预算，可能少分配，不能据此宣称 Docker 已强制文件系统配额。两项新回归 RED 后，与选择器/双池适配/心跳组合 **60 passed, 5 deselected in 0.28s**。
- Task5A 独立生命周期控制器/安全恢复与 Task6B 网络声明管理界面已通过 scoped 独立复审，正在完成父级集成检查，详见后文最新结果。没有真实 Kubernetes 双节点 / Cilium / 身份网关验收；默认仍关闭。

本地实际输出：logs/api-unit-release-check.log、web-full-release-check.log、web-quality-physical.log、physical-full-pg.log、physical-cross-workspace.log、physical-downgrade-green.log、physical-delete-fix1-{red,green,full,mypy}.log、heartbeat-{red,green,pg}.log、disk-admission-red.log、disk-heartbeat-green.log。日志目录本地忽略，不提交凭证和大段测试 traceback。

## 当前已写代码

- 双池auto/specified契约、共享资源选择器；可信SSH指标及公平定期采集；智能体部署原子容量准入、幂等收据、保留旧大资源承诺。
- 容器多宿主注册/管理/实际宿主路由、全局5保有名额、停止计算资源确认与重启预约；本地镜像构建宿主亲和。
- 独立critical owner审批/授权/撤销，MCP申请与服务器候选；Docker数量豁免经DB策略确认生效。
- 网络服务/入站/公开入口的期望状态、作用域与修订；新宿主认证的策略读取/ACK传输。运行层未验证时ACK拒绝，跨实例/公网不假称已生效。
- 默认自动/指定部署界面、多宿主管理、实例配额与独立授权界面；容器网络状态与内部服务声明入口。
- 0060–0063为增量迁移；未执行生产迁移或更改真实远程服务器。

## 真实验证（按范围，不能合并当成完整验收）

- 修改前API非integration基线：548passed、42skipped、750deselected（138.59s）。
- 共享选择器22测试通过；选择器+SSH采样+运行层边界合计60passed（2.41s）。
- 智能体初始PG部署16passed（511.29s）；修订后采样/两项资源记账回归/迁移12passed（96.20s）。新旧全部部署链路最终回归仍需继续。
- 容器多宿主与迁移定向13passed（123.53s）；后续最终混合suite被会话额度中断，日志没有完成摘要，不能算通过。
- 权限+网络声明组合20项：18passed、2failed；失败均是新测试夹具/断言错误（MCP可信入口、helper返回字典与保留邮箱域），最后两项修正后2passed（57.80s）。不是对当前新增宿主传输的最终全套证明。
- 新宿主网络传输：RED404，接通后1passed（19.86s），覆盖认证和未完成数据面验证时不允许成功ACK。
- 新容器网络UI定向1passed（47s），验证打开不发写请求、兼容后端明确不可用。
- 后端Ruff通过；类型检查287源文件通过（在新增宿主网络API之前，需最后重新检查）。前端Node22.23.4 npm ci完成，typecheck/lint通过；完整测试正在运行，已出现失败，不能声称全绿。
- 中途广泛API检查为让出2CPU资源主动中止：63passed/767deselected/KeyboardInterrupt，不算完整通过。旧前端typecheck也中止过；后续修正真实类型错误后已单独成功。

## 必须继续的工作，禁止标记done

1. 前述检查点完整前端和 API 已通过；控制器、心跳、磁盘和新增网络管理修改集成后需最终复验对应范围。不能提高 timeout 或掩盖失败。
2. 历史只读 Docker 核对入口已经实现并验证；继续保持真实不限资源拒绝准入，不静默改限额。
3. Kubernetes后端目前仍被候选筛选排除。独立controller/CLI代码只覆盖部分生命周期和私有Service/策略API回执；尚无完整exec/files/snapshot、生产入口脚本、镜像分发/存储验证和全功能下发链路。不可宣称可用。
4. 私有域DNS策略、跨节点加密/身份与Cilium数据面验证、mTLS身份网关、公网回流/代理绕过防护、TCP/UDP限制和存量连接撤销仍未完成。跨实例通信授权不能凭DB批准直接active；公开入口保持不可用。
5. 物理身份全局账本已实现；完成删除并发修复的独立复审与最终集成检查。
6. 完成真实双节点PoC及故障/未知状态/撤销测试，记录实际版本/镜像digest。当前仅官方kubectl v1.35.10经SHA256校验安装；没有已配置Kubernetes集群/凭据，单元HTTP/CLI fake不是实测网络。
7. 新网络管理UI目前能查看和登记内部服务；源/公网申请、撤销/修改的完整界面仍需补齐并接通真实执行业务。
8. 完整独立审阅、真实日志归档、提交推送及PR。保持T-0222 in_progress；Claude审查，Codex不合并。

源文件级报告与独立审阅位于本检出的.superpowers/sdd/runtime-v2/（gitignored）；关键状态以本文与任务日志为可恢复事实。

## 后续检查点（649e2ab之后）

- 历史资源核对已接通owner+CSRF的POST /api/v1/agents/instances/{id}/resources/reconcile及资源面板按钮。固定Docker inspect只读取身份与CPU/内存字段；检查部署版本/凭据/服务器版本及未完成作业，网络调用不持数据库行锁。不修改运行容器或用户的期望上限。实际不限资源仍拒绝，不会当0。
- 资源解析与核对接口：RED缺少模块/404后，解析2项与真实PG接口1项合计3passed（10.84s）。异步fixture原有缺少datetime导入已修正，不能把此前失败当成功。
- 前端本次相关3文件30passed（46.99s）；修复了新增backend字段的请求断言和撤销后的异步断言。完整并发FE尝试被中断，没有最终摘要；出现过其他页面超时，仍需无竞争地全面复跑。
- Node22 build实际成功（Vite1m18s；exit0），已有大chunk和依赖PURE注释警告如实保留，不为通过而屏蔽。
- CPU归一化load可超过1，原误报未知指标。新回归RED后修正为RESOURCE_PRESSURE，选择器23项+解析2项合计25passed（0.17s）。
- 后端严格类型检查290源文件成功，最新新增代码仍需最终统一复验；Ruff全树通过。原目录git status仍与起始时相同。
- 当前PR为草稿 https://github.com/brianabc121/WorkspaceCore/pull/193，已附加到本聊天；不可合并，T-0222继续in_progress。

新增最终审阅重点：当前闲置运行负载仍有尚未使用的内存上限时，外部进程实际内存占用与平台承诺余量须结合可信容器用量计量，避免仅靠当前free+平台total两道门而漏算未用承诺；晚到上报对重启预约的因果证明也需最终复核。禁止据此草稿声称生产容量严格保证已完成。

## 恢复及继续实施（2026-11-11 23:00 UTC）

环境再次启动时 `/tmp` 被清空，9c03935 后尚未提交的源码、临时依赖和未结束 suite 日志丢失。已按本轮补丁记录恢复；现在 `/workspace/WorkspaceCore-runtime-v2` 是持久实体目录，已不再指向临时目录。原 T-0149 检出的状态未变。新日志在本目录 logs/（本地忽略，不把凭证或大段 traceback 提交到 PR）。

### 新增修复

- 内存准入保留已有容器尚未使用的承诺；SSH 固定只读 Docker 命令和 runner stats 采集可信工作集，可回收缓存不能抵扣。未知工作集不猜成 0，未开始运行的预约完整保留；实时可用内存也保留 10% 总内存余量。
- runner-agent 0.2.0 在采样前领取宿主认证的、90 秒有效的单次票据。平台起始时间用于因果判断，收到报告的时间只供遥测使用；旧报告不能解除之后的重启预约。旧 agent 不再满足新增调度的可信采样条件，升级步骤见 UPGRADE.md。
- 暂停/重启中等状态不能当成已停止释放计算。真实 PostgreSQL 暂停容器回归观察 RED 后修正。
- 新报告锁暴露重启、结果回报的相反锁顺序；两项真实 PostgreSQL NOWAIT 回归均 RED 后修正为实例→服务器池→环境（结果回报为服务器→命令→环境），避免与报告的服务器→环境形成死锁。
- MCP 服务器名/节点名移入 untrusted；目录契约增加两个已授权工具。真实 MCP tools/list 回归发现 env_create 的联合 schema 没有 type=object，已保留两种对象分支并补齐对象约束，未弱化 SDK 校验。

### 重启后已完成的真实检查

```text
pytest tests/test_placement.py tests/test_agent_placement.py tests/test_runner_cpu_metrics.py tests/test_runner_multihost.py -m 'not integration' -q
78 passed, 4 deselected in 0.69s

pytest tests/test_runner_api.py::test_runner_tools_need_the_instance_switch tests/test_runner_api.py::test_paused_container_report_keeps_its_compute_reservation tests/test_runner_api.py::test_report_sampling_requires_auth_and_cannot_be_replayed tests/test_runner_multihost.py::test_delayed_report_does_not_release_restart_and_failed_host_cannot_be_removed -q
4 passed in 18.04s

pytest tests/test_runner_lock_order.py -q
2 passed in 16.12s

pytest tests/test_contract.py::test_v2_mcp_outputs_keep_free_text_under_untrusted tests/test_contract.py::test_v2_mcp_tools_match_the_plan -q
2 passed in 0.26s

pytest -m 'not integration' -q
636 passed, 42 skipped, 784 deselected in 189.02s
```

第一次 runner PG 全套在新 MCP 目录问题处停止：31passed、1failed（95.11s），随后上述工具目录定向回归已经通过；不能把这次失败的完整 suite 当通过。更广 PG、最终严格类型检查及前端检查仍在执行，完成后继续追加真实结果。

测试 PostgreSQL 16.15 / Redis 7.4.12 使用此前已固定 digest 的官方镜像内二进制。Docker VFS 可写层会耗尽根盘，现把本任务测试二进制及库解包到 /tmp、仅调整 ELF 解释器位置后以普通用户在 localhost:55432/56379 运行。它们是真实数据库服务；没有把数据库 fake 成对象，也不把此项当 Docker/集群验收。原 daemon 的其他镜像与容器未清理。临时依赖仍可重建，源码与结果保存在持久目录。

### 前端未结束尝试中观察到的失败（需要无竞争复跑）

这次环境重启中断了完整 `npm run test -- --maxWorkers=1`，没有最终结果，不能标绿。已观察到下列用例超时，尚未证明是否与负载无关：

- news-pages：shows banners, pushes a test and asks the agent for a briefing in mode B。
- approval-center-redesign：sends every filter to the server for both the summary and the list and starts paging again；corrects the countdown with the server clock and, at zero, disables approval and reads again。
- calendar-pages：requires a read refresh after a conflicting or uncertain write (409)。
- dashboard：does not repeat a committed widget creation when refreshing metadata fails。
- model-bindings：saves a target without claiming it is applied, and only offers connections granted to this agent。
- operations-pages：hides old-range metrics while the selected range is loading；shows OpenIM and OpenClaw instances as their own group with readable reasons。
- agents-host：shows the three indicators with the over-threshold one marked in words, then the current values and trends。

完整任务仍未完成。上一节待办中的历史只读资源核对、内存闲置承诺与旧报告因果检查已在代码补齐；跨池物理身份防重、完整 Kubernetes 执行/网关/真实双节点网络、完整网络 UI 与最终审查仍需继续，保持草稿和 in_progress。

后续实际检查：`pytest tests/test_runner_multihost.py tests/test_runner_capabilities.py tests/test_runner_api.py tests/test_agent_placement_integration.py tests/test_agent_runtime_reconciliation.py -q` → **51 passed in 173.01s**；包含 runner-agent 新采样、配额/权限、MCP、智能体资源和历史核对。独立锁顺序 PG 两项另见上述结果。`mypy app`（services/api 工作目录，加载项目 strict+pydantic 插件）→ **Success: no issues found in 290 source files**；Ruff app/migrations/tests 全树通过。此前误在根目录运行 mypy、未加载 API 的插件配置，产生 160 项配置相关错误，已用正确项目命令复验，不据此修改其他模块。

磁盘仍有额外待审项：Docker disk_gb 是预算，不是强制文件系统配额；尚未像内存一样对已有卷/可写层未用承诺和外部磁盘占用做独立核算。正式容量保证仍不能仅凭当前空闲磁盘与待创建预算声称完成。

### 2026-11-12 后续集成检查与审阅

- 物理身份 Task3A（含安装期间删除修复）独立 scoped 复审 PASS / APPROVED，无剩余 Critical / Important。
- `pytest -m 'not integration' -q` → **688 passed, 42 skipped, 798 deselected in 188.57s**。Ruff 全树通过，`mypy app` → **296 source files** 成功。此完整检查在控制器审阅修复之前，不当成修复后的验收。
- 物理身份、迁移、SSH、运行环境、可信指标和未知心跳组合：`pytest tests/test_agent_placement_integration.py tests/test_physical_hosts.py tests/test_physical_host_migration.py tests/test_runner_ssh.py tests/test_runner_api.py tests/test_runner_multihost.py tests/test_runner_metrics_api.py tests/test_runner_heartbeat.py -q` → **69 passed in 211.18s**（含真实本地 PostgreSQL/Redis；FakeSSH 仍非真实远程部署）。
- Task5A 首次 scoped 审阅发现三项阻塞：kubeconfig 跳过 TLS 校验不能仅靠 false CLI flag 覆盖；重启后不同命令必须继续使用原始资源 UID；重发完成回执不能携带已被替换的旧 Pod UID/IP。均已交原实现者补 RED→GREEN，未豁免。完整可信 Kubernetes 遥测仍保持未知，不下发零值报告。
- Task6B 补齐既有接口的来源入站/受限公网声明、三类显式确认撤销、权限/作用域/密码清理、过期和旧缓存地址隐藏。新增 **12 项定向测试通过**；独立 scoped 审阅 PASS / APPROVED，无 Critical / Important。没有接口支持的编辑不模拟撤销重建。完整 FE 复验正在进行。
- 新增 `infra/runtime-v2` 的关闭配置和 systemd 模板，没有安装服务。`systemd-analyze verify --man=no infra/runtime-v2/workspace-runtime-v2.service` 实际 exit 1：预期部署路径 `/opt/workspace/services/api/.venv/bin/python` 尚不存在。未据此声称服务已部署。
- 官方 kubectl v1.35.10 下载 SHA256 核验：`c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327`。实际双节点/Cilium/运行存储仍未测试；当前执行用户有效 capability 为 0，根盘余量不足，没有已配置的集群凭据。

### 控制器三项审阅修复复验

C1/I1/I2 均已修复，原审阅者 scoped 复审逐项 ADDRESSED，规格 PASS / 代码质量 APPROVED，无新 Critical / Important。修复包括拒绝实际选中的 insecure kubeconfig、跨指令/重启保留 allocation+generation 原 UID、防止同代次缺失对象被重新创建、拒绝重发改变了 Pod UID/IP 的旧绑定。

```text
RUNTIME_V2_TEST_KUBECTL=/tmp/t0222-kubectl/kubectl python -m pytest -q tests/runtime_v2
77 passed in 1.64s

# 独立审阅者只重复了新审阅回归文件，含真实 kubectl 与本地 TLS：
13 passed in 1.66s

ruff check app migrations tests
All checks passed!
mypy app
Success: no issues found in 296 source files
```

新回归初始 10 failed；补充的新代次缓存和同代次缺失对象重建各观察 1 failed，再修复。中间完整定向曾 1 failed / 75 passed：移除临时认证数据后的真实 kubectl 在连服务器前提示用户名并 EOF；已诊断、改为运行时生成且仅存临时 kubeconfig 的一次性 token。最终 77 项包括不可信证书拒绝和显式信任临时 CA 的正向对照，不把一次普通连接失败冒充证书校验成功。

原始/修复日志已从 /tmp 复制到持久的本地忽略 logs/task5a-*.log。独立审查及此项实际 TLS 边界不证明真实 Kubernetes RBAC、Pod 生命周期、存储或 Cilium 可用。完整设计尚未验收，保持 PR193 草稿和 T-0222 in_progress。

### 最终本轮运行结果（控制器修复之后）

```text
RUNTIME_V2_TEST_KUBECTL=/tmp/t0222-kubectl/kubectl pytest -m 'not integration' -q
701 passed, 42 skipped, 798 deselected in 211.58s

# Node 22.23.4，全前端串行测试；随后两处仅类型修复另行定向复验：
npm run test -- --maxWorkers=1
78 test files passed, 775 tests passed in 440.52s
```

`web-current-typecheck.log` 初次真实失败 exit 2：面板 Action 联合类型最终 else 不能证明 recordId，测试 getByRole 传入了不支持的 exact 选项。已交原实现者做显式 endpoint 分支与合法测试参数的最小修复；不会把完整运行测试通过当作类型检查通过。修复后的静态结果见下一段。

真实 Chromium 额外烟测通过：Vite 加载现有网络面板，模拟 API 边界，owner 显式提交公网来源声明，检查工作空间/CSRF 请求头；pending 不显示可连接地址，admin 只读，390px 无横向溢出，无 pageerror。截图 `logs/network-owner-{desktop,mobile}.png`。首个临时 HTML 测试壳直接导入预构建依赖导致超时，改用 Vite 的标准 bare module 导入后通过；没有为此修改产品代码。测试不发送真实密码或真实服务器操作，不证明网络数据面通信。

### origin/main 36c6fb5e 集成后的首轮证明

- 主干存储/IM 实现与 origin/main 对比无额外差异；只在分配功能所需的 runner 调用处保留合并后的异步快照接口。
- 对五个迁移逐一与合入前 HEAD 比较，确认函数逻辑逐字未变，仅 revision/header/down_revision 顺延；`alembic heads` 唯一输出 `0065 (head)`。
- `pytest tests/test_agent_placement_migration.py tests/test_runner_multihost_migration.py tests/test_physical_host_migration.py tests/test_storage_settings_api.py -q` → **16 passed in 108.56s**，真实本地 PG 新数据库，验证主干存储表与新迁移链兼容、非空降级拒绝等。
- 全树 Ruff 通过，`mypy app` → **300 source files** 成功；生成的 OpenAPI / TypeScript 已同步，`npm run check:api` exit 0，`collab.py check` 0 警告。
- 合并后的完整 API、前端 lint/typecheck/build/测试仍需完成，不沿用先前基线的通过数。
