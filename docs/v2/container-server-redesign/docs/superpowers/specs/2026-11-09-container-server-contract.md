# 容器服务器：指标与接口约定

**拟议第一方契约，需实施后生成OpenAPI；不是已经支持的能力声明。**
配合[设计规范](2026-11-09-container-server-design.md)与[实施计划](../plans/2026-11-09-container-server-redesign.md)阅读。

## 1. 保持不变

- 页面与API仍用/runner，owner/admin管理，SSH部署仍owner；X-Workspace-Id、CSRF、Idempotency-Key、乐观version及既有工具策略不变。
- 默认每个工作空间一个RunnerHost，报告周期60秒，前端默认30秒读取。部署运行时、认证、回收算法、模板／快照／输出保留规则不在本轮修改。
- RunnerReportContainer／RunnerEnvAdminOut保留旧字段；新增字段可缺省／为null，让旧runner-agent继续对账。新前端不能以新字段缺失为理由清空现有容器列表。

## 2. CPU采样

新增`services/api/app/runner_agent/metrics.py`，只负责读取数字、校验与CPU差分：
- `CPUSample`：`used_cores: float | None`、`window_seconds: float | None`。
- `ContainerCPUSampler.sample(container_id: str, generation: str, cpu_total_ns: int | None, monotonic_ns: int) -> CPUSample`。
- `ContainerCPUSampler.prune(active_container_ids: set[str]) -> None`，删除不再存在的基线，避免内存无限增长。
- generation使用真实容器启动代次（如Docker inspect State.StartedAt）；不能只用env_id或重用名称。若读取不了可靠启动代次，此轮CPU为null，不用猜测值。

算法与单位固定：
1. 读取Docker stats的`cpu_stats.cpu_usage.total_usage`累计纳秒。使用Docker ID及真实启动代次隔离基线；不依赖one-shot可能为空的precpu_stats，不用系统loadavg。
2. 同代次两样本：`used_cores = (cpu_total_ns_new - cpu_total_ns_old) / (monotonic_ns_new - monotonic_ns_old)`；`window_seconds = elapsed_ns / 1e9`。
3. 首样本／重启／重建／负数／缺失／回退／时间不前进返回null并建立或重置合理基线。窗口不足1秒不计算且保留上一个基线，超过180秒则重置为首样本。
4. 有效窗口中CPU增量为0，返回0.0，不能变未知；允许使用超过1核。
5. sampler并发访问有锁；异常只影响本容器CPU。保留容器entry、状态及可用内存，不因stats失败漏掉容器触发“丢失”对账。

测试数值：旧累计10e9ns、时刻100e9ns；新累计11.2e9ns、时刻102e9ns→0.6核、窗口2秒；配额2核→30%。累计增6e9ns、间隔2e9ns→3核，配额4核→75%。

保持现有完整报告周期，不新增后台高频stats循环或独立时序库。Docker字段／inspect形式在锁定Engine/API与gVisor场景核验；若实际实现不同，ADR记录证据与等价计算。

## 3. runner-agent报告扩展

`RunnerReportContainer`新增：
- `cpu_used_cores: float | None = None`：有限数、>=0。
- `cpu_window_seconds: float | None = None`：有限数、1–180。
- 两者必须同时有效或同时null；旧agent不提供则两者null。无CPU能力不影响现有state、mem_bytes或卷报告。

runner-agent在原report流程采集CPU，内存保持memory_stats.usage（字节、含缓存）。Docker返回缺失／负数哨兵时作为未知，而不是使用`or 0`补成零；只有明确的有效0才传0。volume UsageData.Size同理，-1不写成负占用。

后台新增指标只覆盖带平台LABEL_RUNNER且ROLE=env的环境，helper／代理／无标签容器不冒充智能体申请的环境；不扩大平台对宿主容器的管理范围。

报告仍受现有2000项限制；不能截断完整对账报告后让未上报容器被判丢失。超过容量或整体采集失败要明确报告失败／限制并保留原状态，指标逐渐过期，不提交伪造完整报告。

## 4. 存储与接收时间

加法扩展`RunnerEnvironment`，均nullable，旧数据不回填“刚刚”：

| 字段 | 类型 | 口径 |
|---|---|---|
| cpu_used_cores | float | 最近有效窗口平均核数 |
| cpu_window_seconds | float | 对应CPU窗口秒数 |
| cpu_reported_at | timestamptz | 平台接收到有效CPU的时间 |
| mem_reported_at | timestamptz | 最近有效内存上报的平台接收时间 |
| disk_reported_at | timestamptz | 最近有效数据卷字节上报的平台接收时间 |

继续使用已有mem_bytes、disk_bytes，不建历史采样表。处理规则：
- 报告必须属于该RunnerHost／workspace，按环境ID归属检查，不能给其他主机／空间的环境写指标。
- 在原RunnerHosts.report事务中写数值与相应时间，统一服务端UTC；不信任agent墙上时钟，不更新last_used_at或环境version来表示观测。
- CPU缺失／采样无效时清当前CPU与时间；内存测量失败时清当前内存与时间，不能给旧数值盖新时间戳。旧agentCPU始终未知。
- 卷存在且有效则更新disk_bytes/time；完整报告中无有效卷信息时该项未知。归档／删除后卷用量不作为现时占用返回，快照大小用原snapshot_bytes单独展示。
- 报告中途失败而没有提交时保留上次合法值／旧时间，供stale显示；普通host poll/check-in不刷新容器指标时间。
- 生命周期与丢失／stray对账规则保持，采集代码不能借机把stopped改failed或跳过在途命令保护。

## 5. 管理端指标DTO

`RunnerEnvAdminOut`新增`metrics: RunnerEnvMetrics | None = None`（旧服务兼容）；新后端实际返回DTO，旧字段保留。

`MetricStatus = 'ok' | 'stale' | 'unavailable' | 'not_running'`。
`RunnerEnvMetrics`包含：
- cpu_used_cores、cpu_percent_of_limit、cpu_window_seconds：float|null；cpu_reported_at：datetime|null；cpu_status：MetricStatus。
- memory_used_bytes：int|null；memory_percent_of_limit：float|null；memory_reported_at：datetime|null；memory_status：MetricStatus。
- data_volume_bytes：int|null；data_volume_reported_at：datetime|null；data_volume_status：MetricStatus。

口径：
- CPU百分比=`cpu_used_cores / env.cpus * 100`；内存百分比=`mem_bytes / (env.memory_mb * 1024²) * 100`。有效限额<=0时比例为null；比例允许>100，数字不截断，展示条长度可最多100%。
- memory_mb实际按MiB，显示640 / 2048 MiB；数据量按1024换算MiB/GiB；不把GiB标成十进制GB。
- 非running环境的CPU／内存status=not_running，数值为null；stopped不显示伪实时0。CPU／内存未知为unavailable。
- 数据卷对running／stopped等仍持有卷的环境可显示；archived／deleted返回unavailable而非旧占用。
- 有合法值且对应上报时间不早于服务端now−180秒、host在线且未停用，status=ok；早于180秒或host offline/disabled则stale并保留旧数字与时间。边界180秒仍有效，超过即过期。
- 有旧值但无可核实时间的历史记录，作为stale显示并标“上报时间未知”，不能宣称实时。没有值为unavailable。
- CPU、内存、数据卷分别判断，不因一个项目未知清空其他项目。host.last_seen_at只表示心跳，host.report.at与各指标reported_at才用于报告文案。没有metrics DTO的旧服务只允许用已有mem/disk数据作“旧版指标／时间未知”，CPU显示未支持，不把cpus配额当用量。

## 6. 只读接口与统计

复用：
- GET /api/v1/runner/overview：host、environments状态计数、instances分配量和recent回收记录。
- GET /api/v1/runner/environments：现有state/instance_id/lifetime/include_deleted保留；输出每个环境的新metrics，保留旧字段。
- GET /api/v1/runner/runs及/runs/{id}：复用environment_id等当前过滤器与实际响应限制；不为主表每行查输出。

新增`GET /api/v1/runner/environments/{environment_id}`，admin且workspace隔离，返回RunnerEnvAdminOut；跨空间／不存在404。通过现有批量转换逻辑构建，不触发Docker／SSH或执行命令。

现有RunnerAdmin.environments内部limit(500)，需在本轮补充服务端分页而非假定返回全量：
- GET /runner/environments新增q（用途或UUID字符串包含查询，1–100字符，LIKE通配符按字面匹配）、sort=last_used|cpu|memory（默认last_used）、cursor可选（<=512字符）、limit=1–500。API缺省limit仍500以兼容旧UI，新UI显式传50。
- RunnerEnvAdminList新增total:int与next_cursor:str|null；total是相同instance_id/lifetime/state/include_deleted/q条件下的全部匹配数，在limit前计算。旧items保留，不改为另一种响应形状。
- q搜索在SQL过滤、limit前执行，LIKE通配符按文本转义。sort按最近使用降序，或按有效新鲜CPU/内存实测值降序；stale/unavailable/not_running排末尾，UUID升序破同分。排序新鲜判定与DTO一致。
- cursor编码offset与workspace/筛选/排序/limit的摘要，查询不匹配422；游标不是授权依据，所有分页仍重新做scope检查。单页读取，不为了统计自动拉完所有页。
- 实时用量可使记录在两次请求间移动。手动刷新／筛选／排序变化回第一页；自动refetch保留当前页，若页已不存在则回有效页。旧页不累积为全量，迟到响应不覆盖新页；不承诺跨时间点的冻结快照。
- 至少测试501条记录的total、第二页、最后一条用途搜索、稳定fixture下分页无重复／遗漏。数字卡仍取overview全量，表格显示服务端匹配total及分页范围。显式查看deleted时注明与卡片未删除范围的区别。若遇旧响应缺total/next_cursor，只标“已加载N条，总量未提供”，不宣称全量、不启用未经支持的全量搜索／分页；前端正式启用新功能前须完成Task 2服务端升级。

统计：
- total=sum(environments各状态，排除deleted)；running、stopped分别取对应状态；attention=failed。归档、创建中、待删除有独立状态；无需四卡互斥相加。
- 如有include_deleted，删除记录只出列表，不混入“未删除总数”；在界面明确范围。
- 无host／请求失败／失权与真实0分开。未知状态枚举显示安全未知提示，不回落running。
- 实例删除后保留环境记录并显示“原智能体已删除／无归属”，不把它挂到第一个现存智能体；共享环境的申请者与可访问者不同。

## 7. 路由、动作与兼容

- /runner?tab=overview为新默认容器总览；instances为智能体用量；templates/runs/settings保持。旧environments映射overview。
- 查询筛选建议统一为instance_id/lifetime/state/q，详情environment_id；URL只含ID／非秘密筛选，不放程序输出／命令原文／Token。
- 选中environment_id在筛选之外时用新详情GET；多个并行运行按真实列表显示，受限结果标“当前已加载”，不编造全量执行数。
- 动作仍为POST /runner/environments/{id}/actions，body={action,version}，action不增加。先用最新详情version，409刷新，WRITE_UNKNOWN查状态；不能改成盲目乐观宣告完成。
- pin/unpin、归档／删除前的分类提示、快照恢复条件、动作状态限制按现有实现，后端不放宽。
- 增量协议先兼容服务端，再更新runner-agent，最后UI；API／生成类型成套提交。保留部署手册中的旧客户端过渡，不强制让运行中的容器停止升级。
