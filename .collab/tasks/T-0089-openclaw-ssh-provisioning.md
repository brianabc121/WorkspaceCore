---
id: T-0089
title: OpenClaw 全自动部署 B：SSH 自动安装后端（指纹确认、一次性密码换专用密钥、Docker、部署接入包、等待上线）
owner: claude
status: in_progress
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api,tests/e2e,.github/workflows,docs
depends_on: T-0088
created: 2026-11-03
updated: 2026-11-03
---

# T-0089 OpenClaw 全自动部署 B：SSH 自动安装后端（指纹确认、一次性密码换专用密钥、Docker、部署接入包、等待上线）

## 目标
owner 在 OpenClaw 实例页填写一台新服务器的 SSH 地址、端口、账号、密码后，工作台自动在那台服务器上安装并启动 OpenClaw，
连回工作台（外网模式，入口由 T-0088 自动配置）。设计与人类的选择见 `docs/decisions/2026-11-03-openclaw-ssh-provisioning.md`。

分几个 PR：
- B1：asyncssh 依赖与核验；SSH 目标表（迁移）；探测主机公钥（显示 SHA256 指纹）、owner 确认后固定；接口与审计；
- B2：安装任务：一次性密码阶段（校验固定的主机公钥 → 装专用公钥 → 需要时用 sudo 安装锁定版本的 Docker、加入 docker 组），
  专用密钥阶段（上传接入包、安装插件、启动、定时任务），等待实例上线；进度与脱敏日志；
- B3：卸载、重装、吊销专用密钥；CI 端到端（真实 sshd + 真实 OpenClaw）。

## 验收标准
- [ ] asyncssh 锁定版本、许可证与用到的接口在锁定版本上核验（ADR 第 4 节补充）
- [ ] 探测只读取主机公钥、不登录；确认时指纹必须与探测结果一致；之后连接只接受这把公钥，变了就拒绝
- [ ] 目标地址：拒绝回环、链路本地（含云元数据地址）、组播、未指定等地址；域名只解析一次，连接用解析出的地址
- [ ] 密码只在 API 进程内存里：不写数据库、不进 Redis、不写日志与审计；命令输出脱敏后保存
- [ ] 专用私钥用 CredentialBox 加密保存（用途绑定到目标）；可以吊销（从对方 authorized_keys 删除并删掉私钥）
- [ ] Docker 只在 Ubuntu 22.04 / 24.04、Debian 12 上自动安装，来自官方软件源、核对签名公钥指纹、锁定版本
- [ ] 安装完成后实例上线（心跳）；失败时给出失败的步骤与原因；API 重启时进行中的任务标为中断
- [ ] 只有 owner 可以使用；所有读写按工作空间隔离；每一步写审计
- [ ] 单元 / 集成测试（进程内 asyncssh 服务器）与 CI 端到端通过；ruff / mypy / pytest / `collab.py check` 通过

## 背景 / 约束 / 参考
- T-0088（接入入口自动配置）已合并；网页表单在 T-0090
- 接入包与安装步骤：`services/api/app/agent_bundle/`（`CHECKLIST.md` 是已核验的手动步骤）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 03:28 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 03:28 UTC · human · owner unassigned → claude：人类在对话中指派：填写 SSH 服务器后自动安装 OpenClaw 并建立连接（全流程自动化）；选择：密码只用一次换专用密钥、显示指纹确认后才安装、自动安装 Docker。本任务是 B 部分（后端），分 B1（依赖、表结构、指纹探测与确认）、B2（安装任务）、B3（卸载 / 吊销与 CI 端到端）几个 PR
- 2026-11-03 03:28 UTC · claude · 开始任务
- 2026-11-03 03:39 UTC · claude · B1 完成：asyncssh 2.24.1 依赖与核验（ADR 第 4.1 节）；迁移 0021 agent_ssh_targets；地址检查（拒绝回环 / 链路本地 / 组播等，域名只解析一次）；探测主机公钥（不登录，SHA256 指纹）、确认固定、删除，owner 专用、按工作空间隔离、限流、审计；OpenAPI 与 schema.d.ts 已更新。下一步 B2：安装任务（一次性密码阶段、专用密钥、Docker、接入包、插件、启动、定时任务、等待上线）
- 2026-11-03 03:51 UTC · claude · 越界说明：apps/web/src/services/schema.d.ts 是由 openapi.json 生成的文件，接口变化时必须一起更新（apps/web 的 check:api），不改前端代码。Codex 审查 PR #130 的两条意见已修：并发更换与确认（探测后锁行重新检查版本，确认、删除也锁行）、域名某一段过长（模型校验 + IDNA 编码错误归为 dns）
- 2026-11-03 04:14 UTC · claude · B2 暂停：人类在对话中要求先把 GitHub 接入改到网页上（T-0091）。B2 还没有写代码；已核验的设计要点：Docker 锁定版本格式 <版本>~<ID>.<VERSION_ID>~<代号>（ubuntu 24.04/22.04、debian 12，amd64/arm64 全部存在，签名公钥指纹 9DC8…CD88）；asyncssh 给了密码时也会回答单个 Password: 的 keyboard-interactive；插件按 CHECKLIST 在 OpenClaw 服务器上 npm pack 后核对 sha256 再安装
- 2026-11-03 05:09 UTC · claude · 继续 B2（T-0091 已合并）。核验：锁定的 OpenClaw 镜像里有 node、npm 12.2.1、sha256sum、chown、tar，默认用户 node（1000），可以在 OpenClaw 服务器上 npm pack 插件、核对 sha256 后安装
- 2026-11-03 05:35 UTC · claude · B2 完成（待 PR 合并）：POST /ssh/install（202，任务在 API 进程内的后台协程里运行，SshJobRunner；启动时把 running 标为 interrupted，停止时取消）；步骤 connect→inspect→authorize_key→docker→reconnect→upload→pull→plugin→start→schedule→online。密码只经标准输入给 sudo -S，密码阶段结束即清掉并断开，改用专用 ed25519 密钥（加密保存在 client_key_ref，用途 agent_ssh:<id>，已加入密钥轮换）；已装专用公钥且 Docker 可用时重新部署可以不填密码。日志脱敏（密码、实例令牌、Gateway 令牌、PEM 私钥、mwa_ 令牌）。上线判断：这次签发的实例凭证第一次被使用。实测：install-docker.sh 在 ubuntu 24.04 / 22.04 容器里装上四个锁定版本并 hold（debian 12 因本环境代理拦截 deb.debian.org 只核对了索引）；install-plugin.sh 在锁定 OpenClaw 镜像里 npm pack → sha256 一致 → Installed plugin: openclaw-channel。发现并修复：asyncssh run() 只在有 input 时发 EOF，没有 stdin 的命令改接 DEVNULL。测试 tests/test_agent_ssh_install.py 17 个（进程内 asyncssh 服务器在临时目录里真正执行命令，假 docker/sudo/bash/chown/crontab）。下一步 B3：吊销 / 卸载 / 重装，CI 端到端（真实 sshd + 真实 OpenClaw）。越界说明：apps/web/src/services/schema.d.ts 由 openapi.json 生成，一起更新。
