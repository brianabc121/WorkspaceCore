---
id: T-0088
title: OpenClaw 全自动部署 A：一键脚本自动配置接入入口（内部 CA 签发入口证书）
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,scripts,compose.yaml,.env.example,.github/workflows,tests/e2e,docs
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0088 OpenClaw 全自动部署 A：一键脚本自动配置接入入口（内部 CA 签发入口证书）

## 目标
人类要求“在工作台填写 SSH 服务器后，自动安装 OpenClaw 并建立连接”（设计见
`docs/decisions/2026-11-03-openclaw-ssh-provisioning.md`）。自动部署的 OpenClaw 服务器走外网模式，
需要工作台先有可以从公网访问的接入入口；以前要手动准备证书、改 `.env`、给 OpenClaw 那边配置私有 CA。
本任务把这一步做成 `deploy.sh connect` 里的一键配置：内部 CA 签发入口证书，接入包自动带上 CA 与地址映射。

## 验收标准
- [ ] 内部 CA 可以签发入口服务器证书（EC P-256、2 年、serverAuth、SAN 为主机名或 IP），并能判断现有证书是否需要重签（主机名不符、不是这个 CA 签的、剩余不足 60 天）
- [ ] `python -m app.commands.agent_ca issue-server --host H` / `check-server --host H` 可用，非法主机名返回错误码而不是堆栈
- [ ] `AGENT_PUBLIC_INTERNAL_CA=true` 时外网模式接入包附带 `tls/ca.pem`，隧道带 `--cacert`；`AGENT_PUBLIC_ADDRESS` 设置时隧道容器把入口主机名映射到这个地址；非法地址启动时报错
- [ ] `deploy.sh connect` 新增“OpenClaw 接入入口”一步：签发证书、写 `.env`、重启 api / worker、启动入口、握手检查；证书仍有效时不重签；已经手动配置外网入口（未用内部 CA）时不覆盖
- [ ] 单元测试覆盖上述行为；CI 的 agents 任务用内部 CA 签发的入口证书跑一遍外网入口验收
- [ ] 部署教程第 8.6 节与部署文档说明自动配置方式和新变量
- [ ] ruff / mypy / pytest / `collab.py check` 通过

## 背景 / 约束 / 参考
- 用户的选择与三步拆分：`docs/decisions/2026-11-03-openclaw-ssh-provisioning.md`
- 外网入口的既有核验：`docs/decisions/2026-10-31-v2-oc2-agent-ingress-verification.md`
- 后续：T-0089（SSH 自动安装后端）、T-0090（网页表单与进度）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 02:15 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 02:15 UTC · human · owner unassigned → claude：人类在对话中指派：用户在工作台填写 SSH 服务器 IP、端口、账号、密码后，平台自动安装 OpenClaw 并建立连接，全流程自动化；选择：密码只用一次换专用密钥、显示指纹确认、一键脚本自动配置接入入口、自动安装 Docker。本任务是 A 部分（接入入口）
- 2026-11-03 02:15 UTC · claude · 开始任务
- 2026-11-03 02:36 UTC · claude · 实现完成：内部 CA 签发入口证书（issue-server / check-server）、AGENT_PUBLIC_INTERNAL_CA / AGENT_PUBLIC_ADDRESS、接入包附带 tls/ca.pem 与 extra_hosts、deploy.sh connect 新步骤（含内网占位证书、非 root 时改属主）；单元测试与 deploy.sh 假 docker 测试通过；本机用真实 Caddy + 原样接入包的 ghostunnel 隧道验证了 SNI、内部 CA 校验与双向证书；CI agents 任务最后加两步真实运行
