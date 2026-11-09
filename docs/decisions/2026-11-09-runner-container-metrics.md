# 2026-11-09 容器服务器的容器指标：CPU 差分采样、逐项上报时间与新鲜度（T-0184 ~ T-0188）

- 状态：accepted（人类 2026-11-09 在对话中要求按 `docs/v2/container-server-redesign/` “根据效果图和设计文档全部落实”；
  技术细节由 claude 决定）
- 关联：`docs/v2/container-server-redesign/docs/superpowers/specs/2026-11-09-container-server-contract.md`（指标与接口约定）、
  `services/api/app/runner_agent/metrics.py`、`app/domain/runner_metrics.py`、迁移 `0055`。
- 不变：运行服务器（Runner）的认证、SSH 部署、回收算法、模板 / 快照 / 输出的保留规则；`/runner`、`/api/v1/runner`、
  `runner_*` / `env_*` 工具与表名（页面上改叫“容器服务器”，代码与接口不改名）。

## 1. 决定

| 问题 | 决定 |
|---|---|
| CPU 实际用量从哪来 | runner-agent 在原有每分钟上报里读 Docker 一次性 stats 的 `cpu_stats.cpu_usage.total_usage`（累计纳秒），与本机单调时钟一起记作基线，下一次相减：`已用核数 = CPU 时间增量 / 墙钟间隔`。是窗口平均，不是瞬时值，可以大于 1；不用 `precpu_stats`（one-shot 时为空）、不用系统负载，也不把配置的 `cpus` 当用量 |
| 容器重启 / 同名重建 | 基线按 Docker 容器 ID + 启动代次（inspect 的 `State.StartedAt`）隔离；代次变了、ID 变了都当第一次采样（未知）。读不到可靠代次时这一轮 CPU 为未知 |
| 未知与 0 | 第一次采样、计数器回退、读数缺失或为负、窗口 < 1 秒（保留基线）或 > 180 秒（重置）都上报空；只有同一代次、有效窗口里累计时间没变才上报 0.0 |
| 内存 | 沿用 `memory_stats.usage`（字节，含页缓存），不改成 RSS；`memory_stats` 为空（刚停止的容器）时是未知，不再 `or 0` |
| 数据卷 | 沿用 `/system/df` 的 `Volumes[].UsageData.Size`；`-1`（Docker 没算出来）或缺失为未知，不当成 0，也不写成负数。它只是数据卷，不含镜像与容器可写层；不和模板的 `disk_gb` 拼成“磁盘配额百分比” |
| 采集频率 | 不新增线程或高频轮询：只在原有的上报（默认 60 秒）里，对运行中、带 `myworkspace.role=env` 标签的环境各多读一次 inspect。页面默认 30 秒读一次工作台，不会触发 Docker 采集 |
| 单个容器读不到 | 只影响这个容器的 CPU / 内存（上报为空），容器条目照样上报，不会被对账判成“丢失” |
| 协议兼容 | `RunnerReportContainer` 新增 `cpu_used_cores` / `cpu_window_seconds`，可缺省；两者同时有值或同时为空，有限、非负、窗口 1–180 秒。旧 runner-agent 不带这两项照常对账；旧服务端会忽略新字段（Pydantic 默认忽略多余字段） |
| 平台接收时间 | 服务端在原 report 事务里按**服务端 UTC 时间**分别写 `cpu_reported_at` / `mem_reported_at` / `disk_reported_at`（不信 agent 的墙钟），不改 `last_used_at` 与 `version`；某项读不到就清掉这一项的值与时间，不给旧数字盖新时间；普通 poll 不刷新指标时间 |
| 新鲜度 | 180 秒（报告周期 60 秒的 3 倍）。每项分别判断：有值且上报时间不早于 180 秒前、主机在线且没停用 → ok；否则有旧值 → stale（保留数字与时间）；没值 → unavailable；不在运行的环境 CPU / 内存 → not_running；归档 / 删除后数据卷 → unavailable |
| 历史 | 不建历史表或时序库，只存最新一次读数；页面不画曲线 |

## 2. 核验（本会话自己的 Docker，Engine 29.9.2 / API 1.56，cgroup v1，runc）

运行服务器锁定的是 Docker Engine 29.9.1（`2026-11-06-runner-verification.md` 第 1 节），与 29.9.2 是同一个 API 版本。

| 检查 | 结果 |
|---|---|
| `GET /containers/{id}/stats?stream=0&one-shot=1` 的字段 | 有 `cpu_stats.cpu_usage.total_usage`（纳秒）、`memory_stats.usage`；`precpu_stats.cpu_usage.total_usage` 为 0（one-shot 不带上一次的读数，所以要自己差分） |
| 差分的单位 | 一个 `while True: pass` 的容器，两次 stats 间隔 2.0035 秒（单调时钟），增量 / 间隔 = 0.994 核 |
| 重启 | `docker restart` 后 `total_usage` 从 10 894 183 985 回到 36 981 849，`State.StartedAt` 从 `…09:34:14.016Z` 变成 `…09:34:25.257Z`：按代次重置是必要的 |
| 已停止的容器 | stats 仍返回 200，但 `total_usage` 为 0、`memory_stats` 为 `{}`：只对运行中的容器采样，读到空就是未知 |
| `/system/df` 的数据卷 | `Volumes[].UsageData` 为 `{"RefCount": 0, "Size": 57205454}` 这样的形式；文档约定没算出来时 `Size` 为 `-1` |
| 真实 runner-agent 上报 | 用 `app/runner_agent` 的 `Agent.report()` 连本会话的 Docker，两个带工作台标签的容器（一个忙循环、一个 `sleep`）：第一次两个都是未知；7.4 秒后忙的 0.985 核、闲的 0.0；报告通过服务端的 `RunnerReport` 校验 |

gVisor（runsc）下的 stats 在 CI 的 agents 任务里实测（运行服务器验收装了 gVisor），结果写进 `docs/verification/T-0188-container-server.md`。
若 runsc 下累计时间读不到，按本 ADR 上报为未知，不用别的数字代替。

## 3. 升级顺序

1. 先升级工作台（服务端兼容新旧两种报告，新列都可为空）；
2. 再按原来的接入包 / SSH 部署流程升级 runner-agent（不停止已有的环境容器；升级前 CPU 显示“未采集”）；
3. 页面随工作台一起更新：旧 agent 的 CPU 显示“未采集”，内存与数据卷照常显示。
