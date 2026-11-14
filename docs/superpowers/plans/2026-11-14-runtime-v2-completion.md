# T-0224 剩余实现与完整验收执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实用户2026-11-14“全部落实”指令，修复当前CI阻塞、完成运行适配并逐项取得真实验收证据。

**Architecture:** 延续已批准的K3s/Cilium/gVisor、Workspace授权/配额与有界命令控制器。既有Docker行为保持兼容；操作者只在专用非生产资源验收，真实基础设施不可用时明确记录缺失前提，不放宽身份、TLS、配额或数据保护。

**Tech Stack:** Python3.12/FastAPI/SQLAlchemy/PostgreSQL/Redis，Vue3/TypeScript/Node22，固定版本Kubernetes/kubectl1.35.10、K3s1.35.10+k3s1、Cilium1.19.10；可信执行辅助程序使用官方摘要核验的Go1.27.4构建Linux amd64静态二进制，不依赖租户镜像内的解释器。

**Spec:** `docs/superpowers/specs/2026-11-11-runtime-v2-design.md`；网络补充 `docs/superpowers/plans/2026-11-13-runner-network-completion.md`。本计划继续既有批准设计，不重新发明产品路线。

## Global Constraints

- auto默认；specified是硬约束，不静默换机；旧server_id请求保持specified语义。
- 默认5个保有环境，停止/未知/待删除计入；数量豁免不放宽CPU/内存/磁盘。
- 不开放hostNetwork/privileged/Docker socket；OpenClaw服务器仍不开exec。
- 所有运行操作绑定workspace/host/environment/allocation/generation/UID；未知结果保持占额，不能盲目重放写操作。
- TLS与SSH身份校验保持；秘密仅经专用安全通道，不提交token、私钥或生产配置。
- 不自动迁移旧Docker环境，不运行破坏生产数据的迁移/清理，不合并PR。
- 公网验收要求真实LB保留socket来源、两个独立真实外部IPv4来源、允许/拒绝/撤销对照；模拟不能替代。
- 对功能/缺陷遵循RED→GREEN；记录执行命令、退出码、提交、实测层次与缺项。

## Review Focus

- 当前分支与main均占0068：合并图、从旧分支0068及正式main0068升级不能丢数据或误认历史。
- 非幂等exec和文件/快照写入超时不得重放、误报成功或释放容量。
- symlink/路径穿越、输出/归档炸弹、秘密泄露、跨代次目标替换须拒绝。
- 本地PV声明容量不等于实际磁盘硬配额；停止环境仍保留空间，跨节点不能错误挂载。
- 控制面/网关失联与高负载时权限撤销必须有界，不能靠重启目标冒充断连。

## Task 1 — 主干集成与迁移/CI恢复

**Files:** `.collab/tasks/T-0224-runtime-v2-placement-network.md`，`services/api/migrations/versions/*0068*`及后续兼容迁移，`migrations/runtime_schema_compat_0068.py`，真实迁移回归测试。

**Interfaces:** 保留main已发布的桥授权迁移；兼容当前分支历史runtime0068及旧0066/0067形状。迁移后图唯一head，历史表/外键/序列保留，未知结构拒绝。

- [ ] 复现真实PR合并图重复0068及失败CI，读取main0068具体DDL与原历史兼容回归。
- [ ] 将最新origin/main整合当前PR203分支，保留所有协作日志，不推main。
- [ ] 为主干/历史分支已升级状态、空库、数据/序列保留及回退保护写真实PG回归，记录RED。
- [ ] 最小修复迁移图与兼容入口，原/新增回归GREEN；运行upgrade head→downgrade base→upgrade head、Alembic检查、Ruff/mypy。
- [ ] 单独提交；独立审查spec与质量，解决阻塞后推进。

## Task 2 — Kubernetes完整运行操作及控制器恢复

**Files:** `services/api/app/runtime_v2/{runner,controller}.py`及按职责新增的操作模块；`app/domain/runner_runtime_profile.py`、`runner.py`、命令payload/回执接线；`tests/runtime_v2/`及真实PG准入测试。

**Interfaces:** 消费现有RunnerCommand kind/payload及结果契约，沿用host/allocation/generation/UID围栏；扩展有界exec、文件读写、env/secret/初始化、快照与恢复；不接受任意Kubernetes清单或宿主路径。

- [ ] 对照Docker执行器及runner_api既有契约逐项列出payload/输出/错误与大小超时限制，补官方固定版本API核验。
- [ ] 为每类正常操作和路径/权限/输出边界、UID替换及超时不重放写RED，先实现最小可执行闭环，再集成准入。
- [ ] 为快照/恢复/模板明确受信持久存储与镜像语义；未完成原子材料验证前不改generation或申请配额。
- [ ] 领域层只对实际支持的组合开放，拒绝组合仍在写入前失败；Docker回归不退化。
- [ ] 控制器重启/重复投递/丢失回执/部分写入恢复用真实行为与有界fake边界验证；独立审查后提交。

### Task 2A — 可信执行辅助程序（后续控制器消费的独立基础）

**Files:** 新增 `services/runtime-helper/{go.mod,main.go,paths.go,execution.go,archive.go,*_test.go,Dockerfile}` 及说明。
**Interfaces:** 静态Linux amd64二进制，`install`模式只复制自身到已挂载emptyDir；运行模式从固定只读Downward API目录读取Pod UID/allocation/generation，与非秘密argv预期值比较，校验通过后读取有界JSON请求/二进制流。可执行路径由平台挂载且只读，不从/workspace或租户PATH加载。

- [ ] 使用stdlib，`CGO_ENABLED=0`；程序在非root1000/gVisor运行，不需要宿主/网络/SA权限。
- [ ] 协议实现 `exec`、`put_files/get_files/list_files`、秘密文件注入，以及流式tar snapshot/restore；执行输出1MiB、文件20MiB、列表深度3/1000条、初始化单步输出64KiB、超时进程组终止。协议响应兼容现有Docker结果字段。
- [ ] 文件操作以fd-relative/O_NOFOLLOW绑定workspace目录，拒绝绝对/.. /NUL和任何symlink；并发替换不逃逸；写入原子替换；归档拒绝链接、设备、FIFO、路径穿越、重复成员及超过声明大小/文件数的解压。
- [ ] 目标UID/代次不符时，在产生副作用前拒绝；恢复不重放exec/setup/写文件；未知保留，不根据假造的租户文件当作完成回执。
- [ ] 使用真实进程/文件/恶意路径/超时/输出和tar流测试RED→GREEN；构建静态二进制并验证无动态链接。helper镜像必须按digest交付，构建/分发由Task2C和实机验收完成。

### Task 2B–2E — 分阶段集成

2B：扩展控制器安全二进制流、操作结果白名单/秘密擦除、journal不重放、binding_receipts过滤，以及Runner当前generation/UID回执与snapshot part授权。保持未实现操作拒绝。

2C：受信helper init交付和只读挂载/Downward API、卷helper生命周期，环境env/secret/初始化、exec与文件操作；后台工作线程有界，长exec不阻塞网络/遥测/stop。

2D：复用现有加密SnapshotStore的16MiB分片与manifest，停止证明后快照，材料完整性与安全归档预验证后才恢复/改generation，确认快照后才归档删除。

2E：模板只有实际可拉取OCI digest才标ready；有steps的构建使用真实受控builder/registry，缺该设施仍提前拒绝并列为未完成，不能把容器运行shell冒充Dockerfile构建。

## Task 3 — 真实存储、调度与故障恢复验收

**Files:** `infra/runtime-v2/`存储/安装/验收材料，必要的运行层遥测与调度修复，`services/api/tests/runtime_v2/`，`docs/verification/2026-11-14-runtime-completion/`。

**Interfaces:** 当前专用t0224-nonproduction集群与可信Workspace控制面；API创建的真实allocation/UID，不手填active/ACK；硬配额能力需实际证明。

- [ ] 严格SSH核验后只读盘点已有集群、证书、工作负载与数据；保护A已有网络/卷及本任务回执资源。
- [ ] 通过真实API验证auto/specified、并发容量、5/6数量审批/撤销、停止/未知占额；故障时不双建。
- [ ] 存储驱动真实硬配额/写满、保留数据启停、卷节点亲和、删除失败保护与快照恢复摘要。
- [ ] 专用控制器/网关的失联、进程重启、租约过期、代次替换与迟报；记录UID、持续连接与新连接结果。
- [ ] 必要修复按RED→GREEN，独立审查后提交；真实不可用的能力不写PASS。

## Task 4 — 公网与容量/续租验收

**Files:** `infra/runtime-v2/acceptance_two_nodes.py`及必要独立压力脚本/精确部署配置，运行层网关的必要修复，专用证据目录。

**Interfaces:** 既有认证/public-plan、原始socket来源审计、两个独立外部来源产物；同一reviewed_config_sha256；不把Tailscale私网当公网。

- [ ] 盘点可用的真实公网LB/域名/TLS和外部来源；在已授权测试范围准备精确入口，验证无NodePort/SNAT来源丢失。
- [ ] 先采集允许/拒绝baseline，再撤销验证存量断流、新连接拒绝；控制面/目标保持健康。
- [ ] 压力覆盖稳定连接/租约续期、并发拒绝、控制面负载与撤销延迟；记录实际持续时间、数量、P95/P99和资源，不以配置上限代替容量结论。
- [ ] 不具备真实外部条件时，完成可复现脚本和现场阻塞证据，继续其他可执行项，不伪造最终全PASS。

## Task 5 — 完整回归、浏览器、审查与交付

**Files:** Node22前端测试与必要UI修复，`.github/workflows/`必要接线，任务日志/验证报告/PR203正文。

- [ ] 补跑校验摘要的官方kubectl TLS用例；真实PG/Redis全API、迁移、Ruff/mypy/OpenAPI，Node22 lint/typecheck/tests/check:api/build。
- [ ] 浏览器验收服务器选择、实际宿主、审批/撤销、支持/不支持操作及失败输入保留；API/UI/MCP错误一致。
- [ ] 对最终提交核对所有CI，不用历史绿替代新head；跨任务独立最终审查、修复/复验所有阻塞。
- [ ] 保存逐项完成矩阵、真实输出/摘要、清理回执与外部阻塞；推送同PR203并附加，保留未验收生产开关关闭，不自动合并。
