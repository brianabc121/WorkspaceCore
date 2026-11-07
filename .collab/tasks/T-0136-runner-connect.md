---
id: T-0136
title: 运行服务器 R1 接入：迁移 0042、runner-agent、/runner-api 协议、SSH 部署运行服务器
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,infra,docs,tests/e2e,apps/web
depends_on: T-0135
created: 2026-11-06
updated: 2026-11-07
---

# T-0136 运行服务器 R1 接入：迁移 0042、runner-agent、/runner-api 协议、SSH 部署运行服务器

## 目标
运行服务器接入工作台：数据表、runner-agent（Docker 客户端、出口代理、长轮询、指令、对账、指标）、/runner-api/v1/*、SSH 部署运行服务器（方案第 9、12 节）。

## 验收标准
- [x] 迁移 0042（运行服务器、模板、环境、删除条件、运行、指令，实例新列）与升级 / 降级测试
- [x] runner-agent：标准库实现，按 id 去重执行指令，对账只认带标签的，断线时本机守住超时与磁盘
- [x] 出口代理：none / allowlist / all，只连检查过的公网 IP，私有网段拒绝
- [x] /runner-api/v1/* 与接入入口路径；运行服务器凭证（mwr_）
- [x] SSH 部署运行服务器：Docker + gVisor + runner-agent，页面上完成

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
- 2026-11-06 23:07 UTC · claude · 开始任务
- 2026-11-06 23:08 UTC · claude · 后端完成（PR-A）：迁移 0042（升降级与 alembic check 通过）；app/runner_agent（docker、containers、proxy、client、agent、__main__）；/runner-api/v1/poll|commands/{id}/result|report（凭证 mwr_ + 入口请求头，外网要证书指纹）；接入包 app/runner_bundle（compose、install.sh、install-gvisor.sh 核对 sha512）；Caddy 接入入口加 /runner-api/v1/*（64 MB）与 /mcp（32 MB），实测上限生效；worker 每分钟 runner_tick（指令时限、离线、空闲停止、删除条件 at/idle、到期删除）。剩下：SSH 部署运行服务器（页面，随 T-0140）；磁盘告急的本机保护（随 T-0139 水位）。
- 2026-11-07 00:10 UTC · claude · runner-agent 本机磁盘告急保护随 T-0139 完成（使用率超过 95% 停止一次性运行，工作台掉线也生效）；剩“SSH 部署运行服务器（页面）”，随 T-0140 做
- 2026-11-07 00:59 UTC · claude · SSH 部署运行服务器完成：迁移 0044（runner_ssh_targets / runner_ssh_jobs）；app/domain/runner_ssh.py 复用 T-0089 的 SSH 流程（连接、核对指纹、授权专用公钥、上传接入包（不保存密钥与证书私钥）、sudo 或 root 运行 install.sh、等运行服务器上线）、卸载（有环境时拒绝）、吊销；/api/v1/runner/host/ssh*（只有 owner）；运行服务器 → 设置 页面上的 SSH 部署面板（RunnerSshDeploy.vue）；tests/test_runner_ssh.py 与 runner-pages 测试。paths 加 apps/web（面板）。
- 2026-11-07 00:59 UTC · claude · status in_progress → done：四项验收都已完成；真实 Docker + gVisor 的端到端验收在 T-0141
- 2026-11-07 01:28 UTC · claude · CI 的完整冒烟（全部读接口返回 200）发现：还没有运行服务器时 GET /api/v1/runner/host/ssh 返回 404。改成返回空状态（target、job 为 null），写接口仍然 404；加了测试。
