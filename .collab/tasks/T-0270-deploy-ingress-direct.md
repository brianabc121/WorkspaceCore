---
id: T-0270
title: 一键部署直连接入与 C-9 兼容修复
owner: codex
status: done
priority: P1
branch: codex/deploy-ingress-direct
paths: scripts/deploy.sh,services/api/tests/test_deploy_script.py,.env.example,README.md,docs/deploy-guide.md,docs/deployment.md,docs/superpowers/plans/2026-11-17-deploy-ingress-direct.md,docs/verification/2026-11-17-deploy-ingress-direct.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0270 一键部署直连接入与 C-9 兼容修复

## 目标
一键部署收集并保存智能体直连工作台的地址，网站使用 Cloudflare 时无需手改 .env；保留无域名、LAN / Tailscale 与无人值守旧入口行为，修复 Claude C-9 指出的真实 CI 回归。

## 验收标准
- [x] 证书域名与实际连接 IP 分离，已保存地址保留，前台回答传入后台，不放宽双向证书或证书校验。
- [x] 无域名默认地址、CI 就绪提示、旧 DNS 入口保持和其他组件继续执行均有回归覆盖；测试不隐藏注入地址。
- [x] 105 项相关测试通过，Bash / Ruff / mypy / 文档与协作校验通过；Claude 的 5 条行内意见逐条回复并修复。
- [x] 固定源提交 da7af0f 的真实一键配置与自动接入包 CI 步骤均成功；按本次人类直接合并指令，在最终 merge commit 补齐本任务，产品输入保持不变。

## 背景 / 约束 / 参考
PR #239；#50 C-9；#235 通知与集成队列。最初的 ceaead9 本地 99 项通过但 agents 失败，未掩盖该失败；da7af0f 补齐真实默认路径与兼容修复。
本任务号由人类本次指派的 Codex 单次集成窗口在 #235 先预留，再按模板创建。没有修改 workflow、依赖、业务接口或迁移，也没有重写历史任务 / 交接记录。

## 进度日志
<!-- 只追加不改写 -->
- 2026-11-16 19:54 UTC · codex · 本次对话人类明确指派“可用给我直接合并到main了”，由 Codex 完成 PR #239 单次集成；#235 已预留 T-0270，后续默认集成职责仍归登记的 Claude 会话。
- 2026-11-16 19:54 UTC · codex · 已实现与验证的源提交为 ceaead9、da7af0f；105 项本地回归通过。新 head 的安全、Web、协作、Runtime、Compose、新闻与五系统 Docker 校验成功；agents 步骤18（一键配置）及19（自动接入包）均成功。API 全量与后续 SSH 验收仍在运行，按人类直接合并指令记录状态，不声称全部 CI 已通过。
- 2026-11-16 19:54 UTC · codex · 最终集成只补任务、计划与验证元数据，保留功能分支 head，避免为编号再次触发 PR 全套 CI；合并与窗口释放证据记录在 PR #239、#235。
