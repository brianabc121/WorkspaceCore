---
id: T-0271
title: 智能体服务器详情：连接格刷新并显示延迟，初始化后显示 CPU / 内存 / 硬盘与挂载盘并可一键刷新
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: services/api/app/domain/agent_server_resources.py,services/api/app/domain/agent_server_ssh.py,services/api/app/domain/agent_servers.py,services/api/app/models/agent.py,services/api/app/schemas/agents.py,services/api/migrations/versions/20261116_0076_agent_server_latency.py,services/api/openapi.json,services/api/tests/test_agent_server_resources_view.py,services/api/tests/test_agent_server_credentials.py,services/api/tests/test_agent_server_legacy_cleanup.py,apps/web/src/features/agents,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e/agent_ssh_browser.cjs
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0271 智能体服务器详情：连接格刷新并显示延迟，初始化后显示 CPU / 内存 / 硬盘与挂载盘并可一键刷新

## 目标
人类 2026-11-16 在对话里给了“连接与工具 / 智能体服务器”详情页的截图（服务器已就绪、还没部署智能体，CPU / 内存一直“暂无上报”），要求：

1. “连接”格加一个刷新图标按钮，点了重新检测连接；连接建立后还要显示连接的延迟。
2. 服务器初始化完成后显示这台服务器的 CPU（核数与总负载百分比）、内存（已用 / 可用）、硬盘存储以及挂载的硬盘（已用 / 可用）；
   再加一个刷新按钮，一键重新获取这些信息。

原因：页头的 CPU / 内存只读 OpenClaw 实例的整机上报，没部署智能体就永远没有数。平台其实已经有初始化后每分钟一次的
SSH 资源采样（`resource_snapshot`，T-0224 的调度用）和手动刷新接口，只是前端从没显示过。

## 验收标准
- [x] “连接”格右上角有刷新图标（重新检测连接），调用已有的测试连接接口；连接正常时显示延迟（毫秒），“登录前提”清单与测试结果提示里也带延迟
- [x] 延迟取自同一条 SSH 连接上内核测得的 TCP 往返时间（TCP_INFO），不另外建连接、不多发探测包；读不到时不显示，不影响连接结果
- [x] 延迟记在 `agent_ssh_targets.last_connection_latency_ms`（迁移 0076，可空，CHECK 0–60000）；登录失败、换地址、换密码时清空
- [x] 页头一行：连接、服务器环境、CPU（总使用率 + 核数 + 1 分钟负载）、内存（已用 / 总量，已用百分比 + 可用）、硬盘（已用 / 总量，可用 + 挂载盘数）、部署智能体
- [x] 数据优先用平台的 SSH 采样，没有采样时退回智能体上报；都没有时写清原因（初始化后显示 / 等待采样 / 采样失败 / 暂无上报），不画 0
- [x] “服务器资源”一行：采样时间（过期、失败时说明）+“刷新资源”按钮（调用已有的 `POST /agents/servers/{id}/resources/refresh`）；下面列出每个挂载的硬盘（挂载点、设备 · 类型、已用 / 可用 / 总量、使用率条，≥90% 标红）
- [x] 采样脚本只追加挂载盘列表（`df -PkTl`，只要本地 /dev 设备，按设备去重，排除 squashfs 与 /etc、/proc、/sys、/dev 下的单文件挂载，最多 16 个，安全字符），列表坏了只丢这一项，调度用的快照照常有效
- [x] 接口：`ServerSshOut.connection_latency_ms`；`AgentServerOut.resources`（强类型的采样视图，原始 `resource_snapshot` 保留）
- [x] 桌面 6 格、中等宽度 3×2、手机 2 列，390px 无横向滚动
- [x] 后端 ruff / mypy / 相关测试、迁移 up / down / check；前端 lint / typecheck / test / build / check:api 通过；CI 的智能体服务器浏览器验收（真实 sshd）覆盖延迟与刷新资源

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6928197322
- 资源采样脚本与调度（`agent_server_resources.py`）来自 T-0224（codex，handoff；paths 很宽）。本任务只追加展示用的 `mounts`，不改调度读取的字段与校验，
  解析失败时 `mounts` 记为空列表，快照仍可用于调度。PR #203 没有改这几个文件。
- 新迁移 0076 接在 main 的 0075 后面；PR #203 自带 `rv2_*` 迁移，集成时核对 `alembic heads` 只有一个。
- `tests/e2e/agent_ssh_browser.cjs` 在 T-0224 的 paths 里，T-0269 已在同样情况下改过；这里只在初始化成功后加一步检查。
- 刷新资源与测试连接共用每分钟 10 次的连接限流；GET 详情不登录服务器。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 20:58 UTC · claude · 创建任务（owner: unassigned；编号 T-0271 由集成会话在 #235 预留）
- 2026-11-16 20:42 UTC · human · owner unassigned → claude：人类在对话中指派：智能体服务器详情页的连接格加刷新按钮并显示延迟；初始化后显示 CPU / 内存 / 硬盘与挂载盘，并加一键刷新
- 2026-11-16 20:42 UTC · claude · 开始任务
- 2026-11-16 20:54 UTC · claude · 做完：后端——测试连接（与旧公钥退役）登录成功时从同一条 SSH 连接读 TCP_INFO 的往返时间，写进 agent_ssh_targets.last_connection_latency_ms（迁移 0076，CHECK 0–60000；登录失败、换地址、换密码时清空），ServerSshOut.connection_latency_ms 只在 connected 时给出；资源采样脚本追加 mounts（df -PkTl，本地 /dev 设备、按设备去重、排除 squashfs 与 /etc /proc /sys /dev 下的单文件挂载、最多 16 个、安全字符，失败为空），解析时单独校验、坏了只丢 mounts，调度字段不变；MAX_OUTPUT 8 KiB → 16 KiB；AgentServerOut.resources 是 resource_snapshot 的强类型展示视图（sample / stale / attempted_at / error，硬盘总量来自挂载盘之和，旧快照退回 Docker 数据目录分区）。前端——页头加“硬盘”格成一行六格（中等宽度 3×2、手机 2 列），连接格右上角“重新检测连接”图标、显示“延迟 N ms”（<100 绿 / <300 琥珀 / 其余红），CPU / 内存 / 硬盘优先用平台采样、没有时退回实例上报、都没有时写原因；“服务器资源”一行显示采样时间与“刷新资源”按钮，下面列出每个挂载盘；登录前提与测试结果提示带延迟；列表与部署抽屉的“系统 / 资源”也用采样。测试：后端新增 test_agent_server_resources_view.py 26 个（TCP_INFO 读取含真实回环连接、假 df / 本机 df 过滤、resources 视图各状态、刷新接口集成），连接测试 / 换密码 / 旧公钥退役加延迟断言；前端新增 agent-server-resources.test.ts 5 个，页头断言改成六格；e2e 在初始化成功后检查重新检测连接的延迟与刷新资源。本地：ruff / format / mypy（app migrations tests scripts）、export_openapi --check、alembic upgrade / check / downgrade 0075 / downgrade base / upgrade、相关后端测试 80 + 19 + 26 个通过；前端 lint、typecheck、vitest 84 个文件 905 个测试、build、check:api 通过；Vite + Playwright 截图核对 1680 / 1180 / 390 宽度，没有横向滚动。后端全量 pytest 与 CI（含真实 sshd 的浏览器验收）结果见 PR。
- 2026-11-16 20:54 UTC · claude · status in_progress → done：连接格刷新并显示延迟、初始化后显示 CPU / 内存 / 硬盘与挂载盘并可一键刷新，验收标准全部勾选（CI 浏览器验收合并前确认）
