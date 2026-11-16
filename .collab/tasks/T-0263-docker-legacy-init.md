---
id: T-0263
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

# T-0263 修复智能体服务器初始化拒绝 Debian 11 等旧系统

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
