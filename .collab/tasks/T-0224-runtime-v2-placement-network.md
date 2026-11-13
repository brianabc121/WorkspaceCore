---
id: T-0224
title: 按人类确认设计落实双服务器池调度、容器配额审批与跨节点网络
owner: codex
status: in_progress
priority: P1
branch: codex/T-0224-two-server-acceptance
paths: services/api,apps/web,infra,tests/e2e,docs/superpowers,docs/decisions,docs/verification,.collab/tasks,.collab/handoffs,.env.example,compose.yaml,.github/workflows
depends_on:
created: 2026-11-11
updated: 2026-11-13
---

# T-0224 按人类确认设计落实双服务器池调度、容器配额审批与跨节点网络

## 目标
人类于2026-11-11明确授权按已确认设计全面实施。以origin/main a1053c7为基线，落实双池默认自动/可指定调度、5容器配额与独立审批、同实例跨服务器网络、严格服务入站及配套UI。完整方案和实施计划见docs/superpowers/specs/2026-11-11-runtime-v2-design.md及docs/superpowers/plans/2026-11-11-runtime-v2.md。

## 验收标准
- [ ] 双服务器池auto/specified端到端接通，容量预约并发安全，实际宿主可追踪。
- [ ] 默认5保有环境与独立critical权限审批可申请、撤销和审计。
- [ ] 新运行后端跨节点同实例私网，跨实例与公网入站明确鉴权。
- [ ] 前端页面与正式OpenAPI一致，必要测试/构建/迁移通过，实际日志已记录。
- [ ] 独立审查、交付PR和清晰部署/回退文档；未验证的外部环境能力如实列明。

## 背景 / 约束 / 参考
当前主干无进行中冲突任务或打开PR。旧/workspace/WorkspaceCore含T-0149遗留未提交修改，不碰该目录；本任务使用独立检出/workspace/WorkspaceCore-runtime-v2。人类授权跨原模块归属实施本设计，协作协议不改。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 05:10 UTC · codex · 创建任务（owner: codex）
- 2026-11-11 05:10 UTC · codex · 开始任务
- 2026-11-11 08:31 UTC · codex · 人类明确要求全面实施；已从a1053c7新建专属分支，旧T0149未提交工作未动。计划/设计已落盘，双池调度/权限/UI正在集成；纯Placement22测试通过。已部署本地PostgreSQL16/Redis7测试服务，跨节点网络尚未实测，K8s仍未启用。审阅发现重启预约被旧上报释放与旧采样重复使用两处竞态，正在加回归修复。
- 2026-11-11 14:58 UTC · codex · 继续集成：类型检查287文件、Ruff、核心60单元检查及宿主网络认证/未验证拒收回归通过。新建docs/verification/2026-11-11-runtime-v2/PROGRESS.md列明真实结果与未完成工作；Kubernetes调度/通信数据面仍关闭，完整前端suite有失败正在处理，不标done。
- 2026-11-11 15:15 UTC · codex · 开发检查点649e2ab已推送并创建草稿PR #193（不可合并）；历史资源只读核对API及3项回归已在本地补齐，接下来提交并复验。完整FE并发检查受中断且观察到多项超时；容器页串行复跑10通过1失败，已定位最后一项为等待异步关闭的断言竞态并修正。仍保持in_progress。
- 2026-11-11 22:49 UTC · codex · 环境重启清空/tmp，旧临时检出和未结束suite日志丢失；已从远端9c03935恢复为/workspace/WorkspaceCore-runtime-v2实体目录，按本轮补丁恢复容量承诺/可信工作集/采样票据/MCP不可信文本修复，正在重新验证。重启前完整非integration观察621通过2失败，已定位工具目录契约与主机名输出；未结束PG/FE不计通过。
- 2026-11-11 23:03 UTC · codex · 已恢复持久检出；修复闲置内存承诺、单次采样票据/迟到报告、暂停容器资源误释放、MCP主机名untrusted及联合输出schema。实际API非integration636通过42跳过；相关PG51通过173.01s；两项真实DB锁顺序RED后GREEN；严格mypy290文件、Ruff通过。前端完整suite正在无后端测试竞争地重跑。物理身份防重、磁盘余量细化和分布式网络仍未完成，PR193仍草稿。
- 2026-11-12 03:56 UTC · codex · 继续实施：0064全局物理身份账本、两类SSH安装前置占位及报告拒绝不清理已完成；并发删除审阅问题已RED→GREEN修复，原审阅者Task3A PASS/APPROVED。检查点完整API642通过42跳过，FE77文件763项通过；新增未知遥测心跳与实际host绑定10项含PG通过，磁盘预算/10%余量组合60通过。控制器恢复与网络管理UI仍在开发，不标done，PR193保持草稿。
- 2026-11-12 04:15 UTC · codex · 新增独立Kubernetes生命周期控制器/持久恢复与网络管理UI；Task5A三项TLS/UID/旧Pod回执审阅问题均RED→GREEN并由原审阅者复审APPROVED，77定向通过（含官方kubectl真实本地TLS），独立13回归通过；Task6B12项通过且独立APPROVED。最新相关PG69通过，Ruff和mypy296通过；完整FE仍在串行运行。集群/可信K8s采集/完整exec文件快照/网关数据面未完成，后端保持关闭，任务in_progress。
- 2026-11-12 09:23 UTC · codex · 合入最新origin/main 36c6fb5e：主干已使用T-0222作im_send修复，本任务按协议由后合并方改号T-0224，历史进度不重写；保留PR193原head分支名以避免重复PR。主干已占0060存储迁移，本任务五步迁移顺延0061–0065（物理身份0065），基于主干0060；没有运行生产stamp/重置/迁移。正在复验集成。
- 2026-11-12 09:30 UTC · codex · 主干36c6fb5e集成检查：0060存储表+0061–0065本任务迁移16项真实PG回归通过，5个迁移函数体确认仅改编号/依赖；唯一head0065，Ruff与strict mypy300通过，正式OpenAPI/TS及check:api同步。正在跑合并后全套，不把旧701/775当新基线结果。
- 2026-11-12 10:32 UTC · codex · 当前检查点：主干36c6fb5e已合入并推送d0e92f6b；API703通过42跳过，迁移/存储16通过，回收最终整文件9通过155.61s（修正旧可信采样夹具并加强stopACK前后断言，产品准入不变）。Ruff/mypy300及FE lint/typecheck/build/check:api通过。完整FE774通过8超时；无并发六文件75通过1超时，日历单例仍复现。存储懒加载实验未解决并撤回，未增timeout或削弱断言。详见PROGRESS/REMAINING；下次先继续定位FE差异，再按剩余审计实现可信K8s报告、私网持续对账、完整运行适配与身份网关。PR193仍草稿，任务in_progress，无生产迁移。
- 2026-11-12 15:24 UTC · codex · 人类本轮明确要求合并main，覆盖本次Codex交由Claude合并的角色限制；仅合并当前检查点，保持全部CI门槛与merge commit，不改长期协作协议。未完成的K8s/网络数据面继续关闭，验收仍未全完成，任务保持in_progress。合并前复查发现测试/迁移严格mypy范围遗漏，正在补齐并复验。
- 2026-11-12 15:31 UTC · codex · 合并质量检查补充tests/e2e路径：当前无其他执行占用该验收脚本；ci-infra场景26在首份可信报告之前保存模板失败，改为等待真实候选可调度并显式给小型验收负载配置资源、失败立即给出MCP原因。验收后通过真实回收器清除快照并经公开移除守卫释放登记，避免后续同机SSH验收与全局物理身份防重冲突；不写假报告、不删除身份账本。完整后端检查另抓到真实并发预检查锁反转，已用已有并发回归复现，修复中。
- 2026-11-12 15:54 UTC · codex · 按人类合并指令准备交付当前检查点：补齐CI真实范围Ruff524格式/strict mypy520/OpenAPI/collab检查；智能体部署锁序整组11通过，容器核心50通过，新增回执清理/回收/外键锁顺序15最终通过，真实隔离库CLI迁移up/down/up通过。修复均有RED→GREEN，未增timeout/忽略/削弱断言；独立复审源码未发现剩余阻塞。下一步推送同PR193，等待新head全部CI成功再merge commit；完整网络等功能仍关闭，任务in_progress。
- 2026-11-12 20:49 UTC · codex · 1d1446ce完整CI后端1550pass/12fail/16skip；Web782全过、compose/news/Docker安装及真实容器场景全过。旧SSH/有限上限/锁参数测试契约已适配并加强固定公钥/只读探测/认证前拒绝/替换竞态断言：45项SSH-password通过，服务器API最终2通过，旧公钥清理整文件8通过，heartbeat9unit通过。最终Ruff524/mypy520和独立focused复审通过；产品代码及CI配置无新增改动，推送同PR后等待新head全部CI再合并，任务继续in_progress。
- 2026-11-12 21:30 UTC · codex · e642f43a真实多实例/V2浏览器等通过，新增容量诊断脚本因SecretStr直接传SQLAlchemy失败；已改统一create_engine(settings)，并新增执行原脚本的真实PG/子进程回归，RED1fail6.28s→GREEN1pass11.73s，涵盖实际容量允许/拒绝与只读无写入/URL不泄露。Ruff525、mypy521、OpenAPI及独立复审通过，无产品容量/权限改动；再推同PR并等待新head完整CI后合并。
- 2026-11-13 02:09 UTC · codex · 人类2026-11-13明确要求继续完成同实例跨服务器互通、跨实例授权/受控公网入站/撤销断连，以及完整网络接口页面。基于已合并PR193的最新origin/main建立后续分支；延续已批准设计，保留原目录未提交工作，不将接口声明当作生效。
- 2026-11-13 03:07 UTC · codex · 本轮已补私网持续对账、真实TCP租约网关、独立网关权限/端口序列及版本编辑、管理页面、可信节点采集与安全档位；组合局部220通过1跳过，full539文件strictmypy曾通过。继续补自动Pod重启绑定和网关Service/NetworkPolicy精确端口同步，并跑最终全套。人类新增测试服务器SSH握手前errno111，未认证/未改远端；等可达性澄清，详见docs/verification/2026-11-13-network-completion/PROGRESS.md。所有真实跨服务器验证尚未完成，不标done。
- 2026-11-13 07:57 UTC · codex · SSH诊断：当前执行器是Codex Cloud，HTTP代理可达GitHub，但无VPN/TCP grants，测试SSH及官方SSH443均在认证前被拒绝，未发送密码/修改远端；需要受支持VPN/TCP路径或可直连SSH的执行环境。云端恢复清空/tmp且原全套pytest进程不存在，不能计为通过；源码保留，重跑网关45测试通过，Ruff/collab通过。继续in_progress，详见docs/verification/2026-11-13-network-completion/PROGRESS.md。
- 2026-11-13 08:45 UTC · codex · 人类明确取消测试服务器SSH登录与双机远端测试，继续完成先前网络开发；停止所有SSH探测，不再把VPN配置作为本轮开发前置。恢复本地Node22/PG16/Redis和官方kubectl，收尾网关独立就绪与撤销容量回收；真实集群数据面验证继续如实列为未做，不擅自启用生产开关。
- 2026-11-13 09:18 UTC · codex · 网络执行链代码e80ccc72已推送并开草稿PR197：https://github.com/brianabc121/WorkspaceCore/pull/197。真实本地网络组合236通过/179.41s，e80完整Web CI79文件794通过及构建通过；局部本地失败/中断如实列于VALIDATION.md。补齐Compose未传网络参数的部署缺口，真实渲染+Settings回归RED2→GREEN2；增加网关镜像构建检查。继续等待最新完整API/全栈/镜像CI，默认不启用未经真实节点验收的网络，任务仍in_progress；人类已取消外部SSH测试。
- 2026-11-13 10:07 UTC · codex · 完整API CI发现契约问题，已直接本地复现统一422文档与CSRF矩阵2项失败；内部网关误进入浏览器OpenAPI。加强内部接口隐藏契约后RED1，修复仅include_in_schema=False并正式重生成OpenAPI/类型，不改CSRF矩阵。最终契约+完整安全矩阵+网关鉴权8通过/473.35s；check:api/typecheck/Ruff548/strictmypy544/OpenAPI通过。推送同PR197后等待新head完整CI，不合并。
- 2026-11-13 12:41 UTC · human · owner codex → claude：人类在对话中指派（2026-11-13）：claude 直接修好 PR #197 的审查问题并合并，不再等 codex 修改；改好后告诉 codex
- 2026-11-13 14:32 UTC · claude · 按人类指令接手 PR #197：在 claude 分支并入 codex/T-0224-network-completion@4679f7a（未推 codex 分支），修复审查阻塞 1–3（回执被拒只撤这一条绑定；只有第一个网关子记录提升服务版本；已停止环境按 PV 容量上报）、4（desired_policy 改写前对该行加锁重读）、5（三个新错误码文案）和 7（后台刷新不再清空密码），各加回归测试且均先在原代码上失败。后端全量 1732 passed/16 skipped，ruff/mypy 通过；前端 lint/typecheck/798 项/build/check:api 通过。详见 docs/verification/2026-11-13-network-completion/VALIDATION.md 末节。
- 2026-11-13 14:32 UTC · claude · 交接给 codex（continue）→ .collab/handoffs/2026-11-13-1432_T-0224_claude-to-codex.md：PR #197 的审查问题已由 claude 修复并随 claude 的 PR 合并；T-0224 剩余项（真实两节点/Cilium 数据面验证、Kubernetes 完整运行适配、审查建议 6/8/9/10 的核实）交回 codex 继续
- 2026-11-13 15:37 UTC · codex · 接手交接 2026-11-13-1432_T-0224_claude-to-codex.md
- 2026-11-13 15:37 UTC · codex · PR197已合并，审查修复a07628c在最新origin/main 15a4c90；检查已有执行后仅本聊天进行双机测试。SSH严格校验缺可信known_hosts，已请求核对上次两机相同ED25519指纹，未确认前不认证或部署。先复核验收harness与交接测试。
- 2026-11-13 15:40 UTC · codex · 前置复核：Ruff通过，mypy487文件通过，gateway+telemetry63通过；本地开发PG/Redis恢复后gateway-control为1passed/14errors，最新main存在重复0066迁移导致MultipleHeads（alembic heads输出两个0066）。未绕过或改迁移编号；SSH信任指纹仍待人类可信确认，未远端登录/盘点/部署。证据docs/verification/2026-11-13-two-server-acceptance/；生产网络开关未修改，真实验收未执行。
