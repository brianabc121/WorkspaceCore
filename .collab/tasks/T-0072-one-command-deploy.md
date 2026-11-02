---
id: T-0072
title: 一键部署 / 升级 / 清空脚本（scripts/deploy.sh）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: scripts/deploy.sh,docs/deploy-guide.md,docs/deployment.md,README.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0072 一键部署 / 升级 / 清空脚本（scripts/deploy.sh）

## 目标
人类部署时连续踩坑（`.env` 占位符、数据库密码与 `.env` 不一致、忘记迁移、`curl -I` 得到 405 等），要求一条命令完成部署 / 升级 / 清空，
运行前只需按提示输入 `SITE_DOMAIN`、`ACME_EMAIL`、`PUBLIC_BASE_URL`。

## 验收标准
- [ ] `curl -fsSL …/scripts/deploy.sh | sudo bash` 在没有代码的机器上克隆到 `/srv/my-workspace` 后继续执行；菜单：部署 / 升级 / 清空 / 状态
- [ ] 部署只问三个参数（校验格式，`PUBLIC_BASE_URL` 默认 `https://<域名>`），其余自动：工具与 Docker、`.env`、HTTPS 与生产模式、DNS / 端口 / ufw 检查、up、migrate、smoke、HTTPS GET 检查、安装凭据
- [ ] 处理已知的坑：占位符 `.env` 自动重建；数据库密码不一致时自动同步；网段冲突给出提示
- [ ] 升级：本地修改时拒绝；先备份数据库与 `.env`；拉取后用新脚本完成 env / up / migrate / smoke；给出回滚提示
- [ ] 清空：列出将删除的项目与数据卷（含可选组件），可选先备份，输入项目名确认；无人值守必须 `--confirm my-workspace`
- [ ] 支持无人值守参数（`--domain --email --base-url --yes`）；shellcheck 无告警；沙箱端到端测试
- [ ] 部署教程、README、运行手册说明

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 02:40 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 02:40 UTC · claude · 开始任务
- 2026-11-02 02:40 UTC · claude · 人类在对话中要求：写一个自动化脚本，一条命令完成部署 / 清空 / 升级，运行前只按提示输入 SITE_DOMAIN、ACME_EMAIL、PUBLIC_BASE_URL；吸收这次部署踩过的坑（占位符 .env、数据库密码不一致、curl -I 405 等）。Gemini 对话链接因网络策略读不到，等人类粘贴
