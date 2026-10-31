---
id: T-0047
title: Sprint 6 AI（S6-01 ~ S6-03）：等人类恢复 AI 厂商选型后再拆分
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: docs/decisions, docs/v2
depends_on:
created: 2026-10-31
updated: 2026-10-31
---

# T-0047 Sprint 6 AI（S6-01 ~ S6-03）：等人类恢复 AI 厂商选型后再拆分

## 目标
S6-01 ~ S6-03（AI provider 抽象、对话与消息、只读工具与检索、写工具审批与反 Prompt Injection）需要先选定第一版接入的模型接口。
2026-10-31 人类在对话中暂停了这个选型。本任务只是占位，球权在人类：人类恢复选型后，由 claude 拆成核验、契约、实现、验收几个任务。

## 验收标准
- [x] 人类决定第一版接入的模型接口（规范要求接口抽象、不绑定单一厂商），以及是否提供真实密钥用于实测（只放在环境的 Secret 里，不在对话里提供）
- [x] 决定后由 claude 拆分 S6-01 ~ S6-03 的任务（含核验 ADR、契约、实现、A14 与 DoD 的验收），本任务改为 done

## 背景 / 约束 / 参考
- 规范：第 1.2 节（AI 先只读工具，再开发显式审批的写入操作；不得向 LLM 暴露密钥），第 6.1 节 AI 接口，第 9.3 节，第 13 节 Sprint 6，第 15 节 A14，第 16 节
- 人类的暂停记录在 T-0040 的进度日志

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 06:04 UTC · claude · 创建任务（owner: human）
- 2026-10-31 08:57 UTC · claude · S6-04 ~ S6-06 已验收（docs/acceptance/sprint-6.md）。Sprint 6 只剩本任务的 AI 部分（S6-01 ~ S6-03、DoD 前两条、A14），等人类恢复 AI 服务选型后由 claude 拆分实施
- 2026-10-31 12:24 UTC · claude · 人类提供了 V2.0 设计资料包（docs/v2/），其中 AGENT_TASKS_V2 打算替换 S6-01 ~ S6-03；是否替换、应用内 AI 助手是否保留，见 docs/v2/REVIEW.md 第 2 节第 7 条和第 7 节
- 2026-10-31 15:08 UTC · human · owner human → claude：人类在对话中指派：用 OpenClaw 接替原来设计的 AI 操作方案（S6-01 ~ S6-03），“OpenClaw 已经是很成熟的方案，可以直接用”；要求 claude 根据上传的 V2 升级方案重新优化
- 2026-10-31 15:08 UTC · claude · 开始任务
- 2026-10-31 15:17 UTC · claude · 按人类决定写了优化方案 docs/v2/OPENCLAW_PLAN.md：OpenClaw 负责模型、对话、手机与渠道，工作台提供 MCP 工具服务器、权限、审批与审计；按人类补充的要求，两台服务器之间走私网（推荐 Tailscale）。任务拆成 OC-0 ~ OC-5，替换 S6-01 ~ S6-03。方案和规范文件的修订方式等人类批准，见方案第 10 节
- 2026-10-31 15:17 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-31-1517_T-0047_claude-to-human.md：OpenClaw 接替 AI 方案的优化方案已写好（docs/v2/OPENCLAW_PLAN.md），请批准方案并决定第 10 节的 6 项
- 2026-10-31 15:45 UTC · human · owner human → claude：人类在对话中指派：调整 OpenClaw 方案——1. 工作台管理多个 OpenClaw，分布在不同服务器，有的无法走内网，需同时支持内网与外网两种交互方式；2. 密钥放在工作台，OpenClaw 远程请求密钥；3. 与 OpenClaw 的交互放在工作台：用 OpenIM 搭建 Web 端 IM（管理员端），每个 OpenClaw 作为一个用户，经 openimsdk/openclaw-channel 接入；支持拉群（多个 OpenClaw 在群里互相沟通）、消息置顶、免打扰、分组；不提供删除好友；OpenClaw 上线后管理员端自动加为好友
- 2026-10-31 15:45 UTC · claude · 解除阻塞，继续
- 2026-10-31 16:02 UTC · human · 人类在对话中确认：AGPL 许可证没问题（针对 openclaw-channel 插件；记录在方案第 9 节）
- 2026-10-31 16:02 UTC · claude · 方案更新为第 2 版（docs/v2/OPENCLAW_PLAN.md）：多实例登记与接入包；内网、外网两种模式，都由 OpenClaw 主动连回工作台，外网用本机隧道加双向证书；工作台密钥库加 exec SecretRef 取密钥小程序；基于 OpenIM 的管理员端 IM，推荐由工作台后端中转；群聊防刷屏；OpenIM 安全短板的对策；任务 OC-0 ~ OC-7。等人类批准方案第 12 节的 9 项
- 2026-10-31 16:02 UTC · claude · 交接给 human（escalate）→ .collab/handoffs/2026-10-31-1602_T-0047_claude-to-human.md：OpenClaw 方案第 2 版已按三项调整写好（docs/v2/OPENCLAW_PLAN.md），请批准并决定第 12 节的 9 项
- 2026-10-31 16:11 UTC · human · owner human → claude：人类在对话中决定：方案第 12 节全部按推荐执行，并要求重新写一份改造方案。即：1 采纳方案，规范修订由 claude 起草 PR、人类批准后合并；2 管理员端 IM 由工作台后端中转；3 会话置顶与单条消息置顶都做；4 外网模式必须双向证书（OpenClaw 侧隧道）；5 OpenIM 部署在服务器 A 的独立 Compose 项目；6 防刷屏默认 N=8、M=6，禁止实例之间单聊；7 第一版只做文字和图片；8 模型由人类部署时在工作台密钥库录入；9 第一版不做真实模型冒烟，CI 用假模型
- 2026-10-31 16:11 UTC · claude · 解除阻塞，继续
- 2026-10-31 16:15 UTC · claude · 按人类决定写了定稿 docs/v2/TRANSFORMATION_PLAN.md。拆成 T-0052（OC-0 核验）、T-0053（OC-1 契约）、T-0054（OC-2 实例、凭证、密钥库与接入入口）、T-0055（OC-3 OpenIM 与 IM 后端）、T-0056（OC-4 前端）、T-0057（OC-5 MCP 与审批）、T-0058（OC-6 安全测试）、T-0059（OC-7 验收），以及 T-0060（规范修订，需人类批准）
- 2026-10-31 16:15 UTC · claude · status in_progress → done：人类已决定（OpenClaw 多实例 + OpenIM IM + 工作台密钥与审批，按推荐执行），任务已拆分
