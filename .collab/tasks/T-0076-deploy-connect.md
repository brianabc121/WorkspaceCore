---
id: T-0076
title: 一键部署后自动接入 Vikunja、GitHub、n8n（部署脚本 connect）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,services/api/app/commands/connect.py,services/api/app/integrations/provisioning.py,services/api/app/integrations/github.py,services/api/app/domain/auth.py,services/api/app/domain/integrations.py,services/api/app/domain/automations.py,services/api/app/repositories/users.py,services/api/tests/test_connect_command.py,services/api/tests/test_connect_cli.py,docs/decisions/2026-11-02-deploy-connect.md,docs/deploy-guide.md,docs/deployment.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0076 一键部署后自动接入 Vikunja、GitHub、n8n（部署脚本 connect）

## 目标
人类要求：首次安装后由程序自动配置 Vikunja，自动完成 GitHub 的认证、权限检查与同步，n8n 也要自动配置。
之前这三项都要照着部署教程第 8 节手动操作（建账号、建 Token、复制粘贴到工作台）。
人类选择：管理员仍在浏览器“首次安装”页创建，脚本提示后等待回车再接入。

## 验收标准
- [x] `deploy.sh` 安装结束时提示去浏览器创建管理员，按回车后接入；可以跳过；`deploy.sh connect`（菜单第 5 项）随时单独运行，重复运行安全
- [x] Vikunja：启动、建用户（随机密码，只显示一次）、登录建最小权限 API Token（一年有效）、在工作台建集成并测试、同步、注册 Webhook
- [x] GitHub：给出预填好的 Token 创建链接（只读 Issues / Pull requests / Actions，90 天），隐藏输入 Token，列出可访问的仓库供挑选，建集成并逐仓库检查权限、同步；失败时按原因提示（网络 / Token / 权限），可用 `--replace` 换 Token 重试
- [x] n8n：启动、建 owner（随机密码）、只读 API Key（四个 scope，返回的 scope 不一致时不保存）、`X-Workspace-Trigger` Header Auth 凭据，在工作台建连接并测试
- [x] 接入在 api 容器里调用与网页相同的服务：以管理员身份、要求 admin 以上角色、凭证加密保存、写审计；密码与 Token 只经标准输入（Vikunja 建用户的 `-p` 例外，已在 ADR 说明）
- [x] Vikunja 2.7.1、n8n 2.45.7 用到的接口在锁定版本上实测，写进 ADR；GitHub 预填链接与 `/user/repos` 按官方文档（后者未实测）
- [x] 测试：集成测试（真实 PostgreSQL / Redis + 内存版三方服务）、入口单元测试、真实 Vikunja / n8n 上的预配客户端、沙箱全栈 `deploy.sh connect` 端到端（无人值守与伪终端交互）、shellcheck

## 背景 / 约束 / 参考
- `docs/decisions/2026-11-02-deploy-connect.md`
- 核验 ADR：`2026-10-30-vikunja-2.7.1-verification.md`、`2026-10-30-github-rest-and-affine-verification.md`、`2026-10-31-prometheus-grafana-n8n-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 08:20 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 08:20 UTC · claude · 开始任务
- 2026-11-02 08:20 UTC · claude · 人类在对话中要求：首次安装完成后由程序自动配置 Vikunja，并自动配置 GitHub 的认证、权限检查与同步；随后补充 n8n 也要自动配置。人类选择：管理员仍在浏览器“首次安装”页创建，脚本提示后等待回车再继续接入
- 2026-11-02 09:01 UTC · claude · 实测（锁定版本）：Vikunja 2.7.1 的 user create -p / 重复用户退出码 1、POST /api/v2/login（密码不对 403）、POST /api/v2/tokens（服务端权限与请求一致）、DELETE /tokens/{id}；n8n 2.45.7 的 /rest/settings、/rest/owner/setup（只能一次、弱密码 400）、/rest/login（密码不对 401）、/rest/api-keys（只读 Key 建凭据 403）、/rest/credentials。Vikunja CLI 不能从非终端输入读密码，只能用 -p（写进 ADR）
- 2026-11-02 09:01 UTC · claude · 测试：test_connect_command（20 项，真实 PostgreSQL/Redis + 内存版三方服务）与 test_connect_cli 通过；全量 api 测试 619 passed / 14 skipped；ruff、format、mypy（212 个文件）通过；shellcheck 通过。沙箱全栈（项目名 t76，api 镜像用新代码构建）：connect --yes 在没有管理员时提示后退出；建好管理员后 46 秒接入 Vikunja（测试连接全部通过、同步、Webhook 自动注册 1 个项目）与 n8n（owner、只读 Key、X-Workspace-Trigger 凭据、连接 active），GitHub 跳过；审计记录 4 条、操作者是管理员；日志里没有 Token；重复运行跳过。伪终端交互：GitHub Token 输入不回显，列仓库失败转手动输入，连不上 api.github.com 时提示网络原因，--replace 替换已有集成；发现 docker compose exec -T 会读走提前敲下的回答，已对不需要输入的调用接 /dev/null。沙箱访问不了 api.github.com，GitHub 的真实接口没能端到端实跑
- 2026-11-02 09:01 UTC · claude · status in_progress → done：部署脚本 connect：首次安装后自动接入 Vikunja、GitHub（半自动）、n8n
- 2026-11-02 09:57 UTC · claude · PR #117 的 secrets 检查（gitleaks，扫描整个历史）把测试里的两个假密码当成密钥：换成低熵的假值；已推送的提交 92efc64 里的两处在 .gitleaksignore 里登记（附理由；越出任务 paths，只加忽略条目）。本地 gitleaks 全历史扫描通过。agents 的 V2 浏览器验收失败是 apps/web ChatWindow 的消息去重竞态（与本 PR 无关），已在 PR 上说明并另建任务
