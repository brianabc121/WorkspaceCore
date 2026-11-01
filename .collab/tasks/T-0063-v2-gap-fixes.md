---
id: T-0063
title: V2 补漏：在线状态与好友对账、IM 令牌 30 天与到期提醒、接入包工具禁用加固、健康检查、V2 浏览器验收、导出入口、审批卡片来源（设计文档核查）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,infra/openim,infra/caddy,compose.yaml,Makefile,tests/e2e,.github/workflows/ci-infra.yml,docs/deployment.md,docs/api-contract.md,docs/data-dictionary.md,docs/decisions
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0063 V2 补漏：在线状态与好友对账、IM 令牌 30 天与到期提醒、接入包工具禁用加固、健康检查、V2 浏览器验收、导出入口、审批卡片来源（设计文档核查）

## 目标
设计文档核查（2026-11-01，人类要求检查规范与 V2 方案是否落实）发现的 V2 缺口，人类在对话中同意补上（IM 消息保留期保持 365 天）。

## 验收标准
- [x] 方案 4.1：Worker 每 5 分钟用 OpenIM 的在线状态对账实例的在线 / 离线（下线回调丢失时也能纠正）
- [x] 方案 4.4：Worker 每小时对账实例与 owner / admin 的好友关系，缺了就补（不能删好友第 ④ 条）
- [x] 方案 4.3、6.1：OpenIM 的 IM 令牌有效期 30 天；IM 令牌到期前 7 天、客户端证书到期前提醒管理员（`agent_im_token_expiring`、`agent_credential_expiring`，契约第 11 节）
- [x] 方案 6.2、7：接入包 `tools.deny` 显式禁用插件的发图片 / 文件 / 视频工具；多实例端到端核对模型拿到的工具只有工作台工具与 IM 发消息工具
- [x] 规范 §14：OpenIM 各服务、caddy、agent-gateway 有健康检查，`make openim-up` 等待就绪
- [x] 方案第 8 节：V2 前端浏览器验收（IM 主流程、通讯录、实例页、审批页、390px）进仓库并在 CI 的 agents 任务运行
- [x] 方案 4.4：IM 会话导出有前端入口；方案 4.6：审批卡片与审批中心显示来源会话与触发者
- [x] 后端 ruff / mypy / pytest、前端 lint / typecheck / test / build / check:api、相关浏览器验收通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 18:03 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-01 18:03 UTC · human · owner unassigned → claude：人类在对话中指派：按设计文档核查结果补 V2 缺口；IM 消息保留期保持 365 天
- 2026-11-01 18:03 UTC · claude · 开始任务
- 2026-11-01 18:37 UTC · claude · 全部完成：agent_watch（在线状态每 5 分钟、好友每小时、到期提醒每天）、迁移 0018 im_token_expires_at、OpenIM 令牌 30 天、tools.deny 禁用插件媒体工具（真实 OpenClaw 场景 2 核对工具清单）、OpenIM / caddy / agent-gateway 健康检查与 openim-up --wait、审批来源 triggered_by_name（卡片与审批中心）、IM 会话导出按钮、实例页显示 IM 令牌到期、tests/e2e/sprint6_browser.cjs（本机真实 OpenIM 上 10 项通过）接入 CI agents job。越界说明：docs/decisions/2026-10-31-v2-oc1-contract.md 补两条说明（claude 自己的契约 ADR，在 paths 的 docs/decisions 范围内）
- 2026-11-01 18:37 UTC · claude · status in_progress → done：V2 缺口已补齐，检查见 PR
- 2026-11-01 19:24 UTC · claude · 处理 Codex 审查（PR #107）：P1 在线状态对账改为带原状态条件的原子更新，查询 OpenIM 期间被停用的实例不会被覆盖；P2 离线“已提醒”标记改为事务提交成功后再写（confirm_notified）。各加一条回归测试
