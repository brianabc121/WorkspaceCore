# 发布 checklist

> 每次发布（打 tag、部署到正式环境）前逐项勾选：复制本节到发布记录（issue 或 PR 描述）里勾选。
> 命令说明见 `docs/deployment.md`（第 5 节升级与回滚、第 14 节备份、第 15 节扫描与基准）；版本见 `docs/versions.md`。

## 发布记录

- 版本（tag / 提交）：
- 上一个版本：
- 迁移版本：从 `____` 到 `____`
- 升级前的备份快照 ID：
- 负责人与日期：

## 1. 代码与 CI

- [ ] 要发布的提交在 `main` 上，所有工作流都是成功：`ci-api`、`Web CI`、`ci-infra`、`ci-security`、`collab`
- [ ] `ci-infra` 里的各 Sprint 验收、完整冒烟（`make smoke-full`）、A15 备份恢复都通过
- [ ] `python3 scripts/collab.py check` 通过；本次发布涉及的任务都是 `done`，没有未处理的交接
- [ ] `docs/TASKS.md` 与验收报告（`docs/acceptance/`）已更新
- [ ] CI 与镜像用的 Node 版本一致：`ci-web.yml` 的 `node-version` 与 `apps/web/Dockerfile` 的构建镜像同为 22 系列；
      如果 CI 已经用上更新的小版本，确认没有差异或同步升级镜像（`docs/decisions/2026-10-31-release-versions-and-licenses.md` 第 4 节）

## 2. 安全扫描

- [ ] `make scan` 通过（或最新的 `ci-security` 成功）：依赖没有高危或严重漏洞，git 历史里没有密钥，本仓库的镜像没有已修复的高危漏洞
- [ ] 查看第三方镜像的报告（`scripts/security-scan.sh images` 的“只报告”部分）：
  - [ ] **Caddy**（对外入口）：上游有没有包含修复的新版本；有就按第 4 节升级
  - [ ] PostgreSQL、Redis、Prometheus、Grafana、Vikunja、n8n、restic：上游有没有修复版本；暂不升级的写明原因
- [ ] `.gitleaksignore` 新增的条目都写明了理由，不是真实凭据

## 3. 版本锁定

- [ ] compose 文件与 Dockerfile 里所有镜像都是 tag + digest，没有 `latest` 或只写 tag 的镜像：

      grep -rnE '^\s*image:|^FROM' compose.yaml infra/*/compose.yaml services/api/Dockerfile apps/web/Dockerfile | grep -v '@sha256:' | grep -v ':local'

  （没有输出才对）
- [ ] `docs/versions.md` 与实际锁定的版本一致；本次升级的组件已核验并写进对应的 ADR
- [ ] `services/api/uv.lock` 与 `apps/web/package-lock.json`、`tests/e2e/package-lock.json` 已提交，CI 按锁文件安装
- [ ] 许可证没有变化；有变化的组件已记录并确认可以使用（`docs/versions.md` 第 5 节）

## 4. 备份与恢复

- [ ] 正式环境的 `BACKUP_REPOSITORY` 是异地仓库（不是本机目录），定时备份在运行，最近一次成功
- [ ] 本月做过恢复演练：`make restore-verify` 通过，记下快照 ID、耗时与结果
- [ ] 升级前刚做过 `make backup`，快照 ID 已填在上面的发布记录里
- [ ] `.env` 有一份单独保管的副本（不和备份放在一起），`BACKUP_PASSWORD` 也单独保管
- [ ] Vikunja、n8n 等独立组件已各自备份（`docs/deployment.md` 第 5.1 节）

## 5. 配置与密钥（`.env`）

- [ ] `make env` 已运行，`.env` 里没有残留的占位值：`grep -nE '^[A-Z0-9_]+=(GENERATE_|CHANGE_ME)' .env` 没有输出
- [ ] `.env` 的权限是 600，没有提交进 git（`git status` 里看不到它）
- [ ] 正式环境：`APP_ENV=production`；`PUBLIC_BASE_URL` 是 https 地址；Caddy 已启用 HTTPS 与 HSTS（`docs/deployment.md` 第 6 节）
- [ ] 首次安装完成后 `SETUP_TOKEN` 已清空
- [ ] `INTEGRATION_ALLOWED_HOSTS` 只包含确实需要访问的内部主机
- [ ] 监控地址（`PROMETHEUS_URL`、`GRAFANA_URL`）只在内部网络可达
- [ ] **密钥轮换**（按计划，或怀疑泄露时立即执行）：
  - [ ] Vikunja、GitHub、n8n 的 Token / API Key：先在对方系统里生成新的，再到工作台的集成设置里替换，然后作废旧的
  - [ ] `SESSION_SECRET`：
    - 修改后已有会话的 CSRF Token 全部失效，所有用户要重新登录后才能保存修改；
    - 审计日志里的 IP 哈希从此换了密钥，新旧记录不能互相对照。
    - 安排在维护窗口进行。
  - [ ] `CREDENTIAL_ENCRYPTION_KEY`：
    - **目前不支持轮换**：换了它，已保存的第三方凭证就无法解密，要逐个重新填写。
    - 只在怀疑泄露时更换，同时轮换所有第三方 Token。
  - [ ] `BACKUP_PASSWORD`：
    - 不能只改 `.env`：已有仓库会打不开，脚本会报“BACKUP_PASSWORD 与仓库不匹配”；
    - 要用 restic 的 `key passwd`（或 `key add` 加 `key remove`）更换仓库密码，再更新 `.env` 与单独保管的副本。
  - [ ] 数据库密码：PostgreSQL 只在第一次初始化数据卷时读取，修改要在数据库里执行（`docs/deployment.md` 第 4 节）

## 6. 性能

- [ ] `make perf` 在发布环境或固定的本机环境跑过，结果记在发布记录里；
      与规范第 12 节的目标（基础 API P95 < 300 ms、首页 P95 < 500 ms）对比，已知差距见
      `docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md` 第 8.6 节

## 7. 升级与验证

- [ ] 按 `docs/deployment.md` 第 5.1 节升级：先构建、再用新镜像迁移、最后 `make up`
- [ ] 迁移都只新增对象；有不可逆的迁移时，已得到人类确认并写明回滚只能从备份恢复（第 5.2 节）
- [ ] `make smoke` 通过；`make smoke-full` 通过（或用 `SMOKE_EMAIL` / `SMOKE_PASSWORD` 指定的冒烟账号）
- [ ] 登录核对：首页组件、任务、日历、知识库、开发者、运维、通知、自动化都能打开，运维页的核心服务都是“正常”
- [ ] 回滚方案已确认：上一个版本的提交、升级前的迁移版本、快照 ID 都已记下（第 5.3 节）

## 8. 发布后

- [ ] 打 tag（例如 `git tag -a v1.0.0 -m "…"` 并推送），在发布记录里写明变更、迁移与已知问题
- [ ] 观察一段时间：运维页、Prometheus 指标（5xx 比例、延迟）、Worker 心跳、通知中心有没有异常
- [ ] 下一次例行备份成功
