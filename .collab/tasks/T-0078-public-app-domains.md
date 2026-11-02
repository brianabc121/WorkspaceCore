---
id: T-0078
title: 一键部署：接入状态中文显示；Vikunja、n8n 可选用子域名经 HTTPS 对外打开
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,services/api/app/commands/connect.py,services/api/tests/test_connect_command.py,infra/caddy/https.Caddyfile,infra/caddy/apps-enabled,compose.https.yaml,infra/n8n/compose.yaml,.env.example,.gitignore,docs/deploy-guide.md,docs/deployment.md,docs/decisions/2026-11-02-deploy-connect.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0078 一键部署：接入状态中文显示；Vikunja、n8n 可选用子域名经 HTTPS 对外打开

## 目标
1. 一键部署结束时的「接入状态」直接显示 `active` / `none`，人类看不懂，改成中文。
2. 工作台里「在 Vikunja 中打开」「打开 n8n 编辑器」指向 `http://localhost:3456` / `:5678`（管理界面只绑定本机，要 SSH 隧道）。
   人类选择让 Vikunja 与 n8n 都能用子域名经 HTTPS 直接打开；默认仍不对外。

## 验收标准
- [x] 接入状态显示「已接入 / 未接入 / 凭证失效 / 暂时连不上 / 已停用 / 已保存，还没通过测试连接」，并显示 Vikunja、n8n 的打开地址；connect 命令的提示同样用中文
- [x] `deploy.sh connect` 在 HTTPS 模式下询问 Vikunja / n8n 的对外子域名（默认不对外；建议 `vikunja.<SITE_DOMAIN>`、`n8n.<SITE_DOMAIN>`；输入 `-` 取消；无人值守沿用 `.env`）
- [x] 生成 `infra/caddy/apps-enabled/*.caddy`（写入具体域名，gitignore），Caddy 导入；先校验再重启，不通过就撤销
- [x] `.env`：`VIKUNJA_DOMAIN`、`N8N_DOMAIN`、`N8N_PROXY_HOPS`（新）；公开地址、n8n 的 Secure Cookie 与代理跳数随之设置 / 恢复
- [x] `connect public-urls` 更新工作台里保存的打开链接（带版本号、写审计、状态不变）
- [x] 测试：集成测试（public-urls、中文提示）；shellcheck；沙箱 HTTPS 全栈：子域名转发、Secure Cookie、链接更新、取消对外

## 背景 / 约束 / 参考
- `docs/decisions/2026-11-02-deploy-connect.md` 第 6 节（做法与风险）
- 核验：caddy 2.11.7 导入空目录合法；n8n 2.45.7 的 `N8N_PROXY_HOPS`、`N8N_SECURE_COOKIE`（`@n8n/config`）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 14:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 14:15 UTC · claude · 开始任务
- 2026-11-02 14:15 UTC · claude · 人类在对话中要求：接入状态里的 active/none 改成中文；工作台里「在 Vikunja 中打开」「打开 n8n 编辑器」指向 localhost，人类选择让 Vikunja 和 n8n 都用子域名经 HTTPS 对外（默认仍不对外）
- 2026-11-02 14:31 UTC · claude · 测试：connect / deploy / init_env / https 配置相关 39 项通过；ruff、format、mypy（212 个文件）通过；shellcheck 通过。沙箱 HTTPS 全栈（项目 t78，SITE_DOMAIN=ws.localhost，Caddy 内部 CA）：无人值守显示「已接入 / 未接入」与打开地址；伪终端选择对外后生成两个站点、.env 更新、Caddy 校验后重启；vikunja.ws.localhost /api/v1/info 与页面 200、n8n.ws.localhost /healthz 与页面 200、主站 200，HSTS 等头正确；Vikunja frontend_url 为新地址；n8n 经 HTTPS 登录得到 Secure Cookie；工作台两个打开链接已更新；输入 - 取消后站点删除、.env 恢复、子域名不再服务。真实域名的 Let's Encrypt 签发没在沙箱验证（与主站同一机制）
- 2026-11-02 14:31 UTC · claude · status in_progress → done：接入状态中文显示；Vikunja、n8n 可选用子域名经 HTTPS 对外打开
