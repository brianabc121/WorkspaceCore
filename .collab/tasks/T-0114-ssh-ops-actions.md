---
id: T-0114
title: 远程运维动作：SSH 固定动作、二次认证、输出脱敏与保存、审计、实例页面板（迁移 0032）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0109
created: 2026-11-04
updated: 2026-11-04
---

# T-0114 远程运维动作：SSH 固定动作、二次认证、输出脱敏与保存、审计、实例页面板（迁移 0032）

## 目标
按方案第 6.1 节（D1 选 A）：通过现有 SSH 通道对 SSH 部署过的实例执行固定的运维动作，命令写死、不拼接输入，高风险动作要二次认证，输出脱敏后保存 7 天，审计不记原文。

## 验收标准
- [x] 迁移 0032：运维动作记录（动作、状态、退出码、脱敏输出、到期时间）
- [x] 动作：查看状态、查看日志、重启 OpenClaw、重新拉起全部服务、清理无用镜像、立即上报；同一实例同时只跑一个；超时 60 / 180 秒
- [x] 只限 owner / admin；重启 / 拉起 / 清理要重新输入密码；输出最多 64 KB、脱敏；审计
- [x] 实例页“运维”面板；后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_OPS_PLAN.md`（人类 2026-11-04 在对话中说“给我根据这份方案 全部落实”：批准方案，D1 ~ D7 按推荐执行）
- 核验：`docs/decisions/2026-11-04-agent-ops-verification.md`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 09:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 11:26 UTC · claude · 开始任务
- 2026-11-04 11:26 UTC · claude · 后端：迁移 0032（agent_ops_actions：动作、服务、状态、退出码、脱敏输出、7 天到期；部分唯一索引保证同一实例只有一个 running）；app/domain/agent_ops.py 写死六个动作的命令（status / logs 三个固定服务 / report / restart / up / prune），经部署时确认的主机指纹 + 专用密钥执行，超时 60 / 180 秒，在 API 进程内与 SSH 部署共用 SshJobRunner（新增 launch，重启时 recover 把进行中的动作标为中断），部署 / 卸载 / 吊销与运维动作互斥；输出先脱敏（mwa_ 令牌、Authorization、Bearer、sk-、JWT、token/secret/password/api_key 键值、私钥块）再存，最多 64 KB；二次认证抽成 app/domain/step_up.py，审批与运维共用失败计数；审计 agents.ops.started / finished 只记动作与退出码；Worker 每天删除过期记录。接口 GET/POST /agents/instances/{id}/ops、GET /ops/{action_id}（owner / admin）。前端：实例卡片“运维”面板（固定按钮、日志来源、重启 / 拉起 / 清理先输密码、输出与最近记录、未 SSH 部署时的说明）。
- 2026-11-04 11:26 UTC · claude · 检查：ruff / format / mypy（181 个文件）通过；pytest 运维动作 9 项（真实走一遍 SSH 部署再执行，含脱敏、二次认证、互斥、超时、admin 可用 member 不可、清理）+ SSH 部署 / OC-6 安全 / 迁移一致性共 80 项、契约 49 项通过；web lint / typecheck / vitest（44 个文件 428 项）/ build / check:api 通过；运维面板亮暗主题与 390 宽度浏览器检查无溢出、无报错。真实 Docker 上的命令输出在 T-0115 的 agents CI SSH 验收里核对。
- 2026-11-04 11:26 UTC · claude · status in_progress → done：远程运维动作完成：六个固定动作、二次认证、脱敏输出保存 7 天、审计、实例页运维面板；真实 Docker 上的输出在 T-0115 验收
