---
id: T-0265
title: 修复智能体服务器初始化拒绝 Debian 11 等旧系统
owner: codex
status: done
priority: P1
branch: codex/t0263-docker-legacy-init
paths: services/api/app/domain/agent_ssh_install.py,services/api/app/domain/agent_server_ssh.py,services/api/app/agent_provisioning/files/install-docker.sh,services/api/tests/test_docker_legacy_install.py,.github/workflows/ci-infra.yml,docs/deploy-guide.md,docs/decisions/2026-11-16-docker-legacy-init.md,docs/superpowers/plans/2026-11-16-docker-legacy-init.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0265 修复智能体服务器初始化拒绝 Debian 11 等旧系统

## 目标
修复页面初始化服务器时因系统白名单与统一 Docker 锁定版本而拒绝 Debian 11 的问题；同时覆盖主站部署脚本已兼容的 Ubuntu 20.04。分别使用官方仓库核验过的固定版本，保留已有 Docker 的沿用行为。

## 验收标准
- [x] Debian 11、Ubuntu 20.04 的 amd64 / arm64 初始化不再被白名单拒绝；其他未支持系统仍提示手动安装。
- [x] 安装脚本按系统选择存在的固定版本，签名验证与 hold 保留，不升级已有 Docker。
- [x] 回归测试、真实容器安装验证、相关 lint/typecheck 与协作校验有实际结果；CI 扩展旧系统安装矩阵。

## 背景 / 约束 / 参考
用户报告 Debian 11 bullseye、x86_64、root、未安装 Docker 的初始化失败。根因是 Python 与 Shell 的双重系统白名单，以及 Debian 11 仓库没有统一锁定的 29.9.1 / 2.4.2 / 5.6.0 组合。版本证据与边界见 docs/decisions/2026-11-16-docker-legacy-init.md。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 13:29 UTC · codex · 创建任务（owner: codex）
- 2026-11-16 13:31 UTC · codex · 开始任务
- 2026-11-16 13:31 UTC · codex · 按用户本次初始化故障要求修复。T-0234 仍声明 services/api/tests、.github/workflows、docs 的宽路径，但相关实现已合并 main；本任务新建独立测试/ADR/计划，只改安装矩阵和部署指南的相关行，不修改其 MCP 管理器实现。依 AGENTS 第 9 节复用现有隔离目录。
- 2026-11-16 13:37 UTC · codex · 已复现：原脚本在干净 debian:11 返回 unsupported_os debian 11 / exit 3。新回归首次 8 个旧系统用例失败；另 1 个已有 Docker 夹具缺 docker_ok 标记已修正。实现后 test_docker_legacy_install + test_agent_server_password_jobs + test_agent_server_credentials 共 33 passed（真实 PostgreSQL/Redis + 进程内 SSH）；make lint-api 完整 ruff/format/mypy 通过。独立只读审查未发现可行动问题。
- 2026-11-16 13:39 UTC · codex · Ubuntu 20.04 干净容器安装实测：仅为当前 Cloud 代理挂载系统 CA，保留 TLS 与签名校验；四包版本与 hold 均正确，随后因容器没有 init/daemon 按预期 docker_not_running / exit 5。CI 中同一段精确版本校验代码对实际输出验证通过。Debian 11 干净容器停在系统 apt 仓库的 gnupg 等依赖包 404，未完成真实安装；两个架构四个 Docker 包的官方索引均已核验，CI 覆盖实际安装。
- 2026-11-16 13:39 UTC · codex · status in_progress → done：Debian 11 / Ubuntu 20.04 固定版本兼容修复完成。33 个相关测试通过；make test-api 946 passed / 901 skipped（未设全量集成环境）；make lint-api 通过；Ubuntu 20.04 真实安装及四包 hold 通过。Debian 11 实装受系统依赖源 404 阻塞，arm64 仅核验官方索引；CI 安装矩阵已扩展。独立审查无阻塞问题，生产未部署，交付 PR 待 Claude 审查合并。
- 2026-11-16 13:44 UTC · codex · status done → in_progress：用户要求合并 main；合并前发现 Debian 11 docker-pins CI 同样在安全更新源依赖包 404 失败。已核对官方 archive.debian.org 相同包 200、原 deb.debian.org/security.debian.org 404，正在补齐已归档安全源兼容；不跳过失败检查。
- 2026-11-16 13:48 UTC · codex · 用户在本次对话明确要求合并 main，本次据此直接执行合并，覆盖仓库默认的代理合并分工；仍遵守不跳过失败检查。Debian 11 404 已确认来自 bullseye-security 官方源归档，不是单纯本机环境限制：相同包旧入口 404、archive.debian.org 200，官方说明 LTS 2026-08-31 结束。脚本仅迁移两个官方旧源，保留协议/签名配置/其他套件/自定义镜像与备份；新增迁移回归在旧脚本 2 failed / 2 passed，修复后非集成用例 7 passed。
- 2026-11-16 13:51 UTC · codex · status in_progress → done：已补齐 Debian 11 安全源归档迁移：传统 list / deb822、精确 suite 与官方 URI、协议和 Signed-By 保留、备份幂等、忽略注释中的套件名。迁源回归旧脚本2失败、修复后7通过；审查发现的宿主Docker夹具与注释误匹配均已修复并验证。干净 Debian11 容器四包精确版本/hold实际通过，随后无daemon按预期exit5；CI校验代码对真实输出通过。完整后端950通过901跳过，lint/类型通过；最新范围修正目标用例再次7通过。等待新提交远程CI后按用户要求合并。
- 2026-11-16 14:46 UTC · codex · 合并前 main 已合入 PR #231，另一个前端任务使用了 T-0263，本任务改号 T-0265 并同步 main（含 CI 用 127.0.0.1 避免 localhost DNS 偶发超时）。上一提交其他CI全通过，智能体端到端在重启 OpenIM 后发生RPC服务发现/连接失败，11项后续消息验收失败；相同PR上一轮此组完整通过，Docker初始化代码尚未执行。新提交重新运行CI验证，不跳过失败检查。
