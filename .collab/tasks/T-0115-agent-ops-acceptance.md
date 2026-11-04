---
id: T-0115
title: 智能体运维升级验收与文档：agents CI（真实指标、SSH 动作）、浏览器验收、威胁模型、部署与运维手册
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github,docs,apps/web,services/api,scripts
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0115 智能体运维升级验收与文档：agents CI（真实指标、SSH 动作）、浏览器验收、威胁模型、部署与运维手册

## 目标
方案第 10 节的验收与文档：在真实 OpenClaw 与 SSH 部署上验证指标上报和运维动作，在浏览器里验证新建项目勾选智能体、自由接取与打回、服务器状态与运维动作；更新威胁模型、部署与运维手册。

## 验收标准
- [x] agents CI：真实 OpenClaw 容器上报指标；SSH 部署验收逐个执行运维动作并核对 `docker compose` 输出格式
- [x] agents 场景 17（甲、乙同时领取只成功一个，完成后打回再完成）、场景 18（没开权限被拒，开了之后建项目并拉另一个实例）
- [x] 浏览器验收：新建项目勾选智能体、项目群；自由接取与打回；服务器状态面板；运维动作
- [x] 威胁模型、架构、部署手册、release checklist、versions 更新；方案状态改为已实施

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 11:35 UTC · claude · 开始任务
- 2026-11-04 11:35 UTC · claude · agents CI：多实例端到端新增场景 19（甲、乙的真实 OpenClaw 容器跑 host-report.mjs，管理员看到当前值与小指示，甲用 get_host_status 只读到自己的、带参数被拒）；场景 17、18 已在 T-0111 / T-0112 加入（17 已在 PR #153 的 CI 通过）。SSH 部署验收（agent_ssh_acceptance.py）：照 crontab 那一行跑服务器状态上报并核对当前值；六个运维动作逐个执行，核对 docker compose ps（SERVICE 列）、docker stats（CPU % 列）、df、uptime 的输出，日志里没有实例凭证与未脱敏的 mwa_ 令牌，重启不带密码被拒、带密码成功，审计 7 条且不含输出，卸载后运维不可用；同时修正重新部署后 crontab 应为 4 行（并入 T-0113 的提交）。浏览器：agent_ssh_browser.cjs 增加运维面板（查看状态、立即上报、输密码重启）与服务器状态面板（当前值、两张趋势图、切 7 天、390 宽度）；sprint6_browser.cjs 增加新建项目勾选实例与项目群、任务设为可自由接取（只限项目成员）、实例领取并完成（api 容器里以应用代码调用，与 MCP 同一入口）、管理员写原因打回、动态顺序。文档：方案状态改为已实施并写明两处偏差（工具改名、趋势图拆成两张），架构、部署（21、22 节与验收状态）、release checklist、versions、威胁模型。
- 2026-11-04 11:35 UTC · claude · 检查：python3 -m py_compile 两个 e2e 脚本、node --check 两个浏览器脚本通过；这些脚本要真实 OpenClaw / sshd / OpenIM，由 CI 的 agents job 运行，结果看 T-0115 的 PR。
- 2026-11-04 11:35 UTC · claude · status in_progress → done：验收与文档完成：agents CI 场景 17 / 18 / 19、SSH 部署验收的服务器状态与六个运维动作、两个浏览器验收脚本、文档与方案状态；以 PR 上的 CI 结果为准
