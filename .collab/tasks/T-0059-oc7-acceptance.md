---
id: T-0059
title: OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/acceptance,docs/TASKS.md,docs/deployment.md,docs/threat-model.md,docs/data-dictionary.md,docs/architecture.md,tests/e2e,.github/workflows/ci-infra.yml
depends_on: T-0056, T-0057, T-0058
created: 2026-10-31
updated: 2026-11-01
---

# T-0059 OC-7 Sprint 6 AI 部分验收：CI 多实例端到端、部署与威胁模型文档、备份覆盖 OpenIM、验收报告、勾选 TASKS（S6-01 ~ S6-03、DoD、A14）

## 目标
Sprint 6 AI 部分的整体验收（方案第 8 节）。

## 验收标准
- [x] `ci-infra` 新增多实例端到端任务：工作台加 OpenIM，实例 A 走内网、实例 B 走外网隧道，都用假模型；方案第 8 节 14 个场景全部自动化
- [ ] 文档：`docs/deployment.md`（服务器 A 的变更、OpenClaw 服务器检查清单、两种网络模式）、威胁模型、数据字典、架构
- [ ] A15 恢复演练覆盖 OpenIM；`docs/acceptance/sprint-6.md` 补充 AI 部分；勾选 `docs/TASKS.md` 的 S6-01 ~ S6-03
- [x] ci-infra 运行 `tests/e2e/agent_ingress_acceptance.py`（接入入口、双向证书、本机隧道；测试证书在 CI 里生成）（OC-2 留下）

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 11:53 UTC · claude · 开始任务
- 2026-11-01 11:54 UTC · claude · 第 1 批：ci-infra 新增 agents job（与 compose job 并行）：生成 .env 与两张自签名测试证书（intranet.test / public.test，只在 CI 里用），make up → make migrate → make agents-up（导出内部 CA、启动 agent-gateway），然后运行 tests/e2e/agent_ingress_acceptance.py（13 项：内网/外网入口、双向证书、本机隧道、/mcp initialize、入口错配、吊销、伪造请求头）。本机在 b7e2e 栈上用同样参数跑过 13 项全部通过（T-0058）。验收标准第 4 项待 CI 通过后勾选。下一批：多实例端到端（OpenIM + 两个真实 OpenClaw 实例 + 假模型，方案第 8 节 14 个场景），然后文档、A15 覆盖 OpenIM、验收报告
- 2026-11-01 12:47 UTC · claude · 第 2 批：tests/e2e/agents_acceptance.py + fake_model_server.py，方案第 8 节 14 个场景全部自动化（两个真实 OpenClaw 2026.9.36 实例 + openclaw-channel 0.3.3 + OpenIM 补丁镜像 + 假模型；甲内网直连 read_write、乙外网经本机隧道 read）；ci-infra 的 agents job 增加 make openim-build / openim-up 与这一步。本机 b7e2e 栈上完整跑一遍：场景 9 之外全部通过，场景 9 是脚本用了不存在的单条日程查询接口，改成按区间列出后在保留的现场重跑通过（实例经 tool_call 调 myworkspace__delete_calendar_event 只生成待审批、IM 出现审批卡片、二次认证批准后才执行、另一条参数不同的仍待审批）。验收中发现并修复的产品问题：1) 接入包 openclaw.json 带 _comment，锁定版本拒绝未知键无法启动 → 去掉；plugins 改成 allow + entries。2) 配置引用了还没装的 openim 渠道时 plugins install 被拒 → CHECKLIST 改为用临时空配置（OPENCLAW_CONFIG_PATH）安装插件。3) 接入网关拦了 SDK 要用的 /friend/get_designated_friend_apply、/friend/get_designated_friends、/group/get_group_members_info，插件里的拒绝没人处理导致 OpenClaw 退出或发消息失败 → 放行并按工作空间/群成员校验参数（新增测试）。越界说明：上述修复改了 services/api/app/agent_bundle、services/api/app/domain/im_gateway.py、services/api/tests、infra/openclaw/README.md（原属已完成的 T-0055/T-0057/T-0058，D-0007 下都由 claude 负责，没有进行中的其他任务占用）。已知上游行为（写进验收报告）：OpenIM 重启后 SDK 重新同步时抛出未处理的 TypeError，OpenClaw 进程退出、容器约 45 秒后重启重连，这段时间收到的消息插件不补处理（工作台投影不缺），脚本最多重发 3 次；实例间私聊 message 工具结果显示 done，但工作台与乙都没有收到。下一批：文档（deployment / threat-model / data-dictionary / architecture）
