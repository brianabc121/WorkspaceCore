# My Workspace V2.0 设计资料（存档）

人类在 2026-10-31 提供了 V2.0 设计资料包（`MyWorkspace_V2_Codex_开发升级包.zip`，8 个 Markdown 文件，
sha256 `2c0f41d55f9369c95a0aa83c64f05e905c9a48df353bd677765a6f3d4e3386a2`）。本目录原样保存解压结果，并附上 claude 的评审。

**状态：V2 的 AI 与智能体部分已定稿。** 2026-10-31：
- 人类决定用 OpenClaw 接替 AI 方案；
- 又提出三项调整：多实例与内外网接入、密钥集中、OpenIM 内置 IM；
- 最后决定“按推荐的来”。

定稿是 [`TRANSFORMATION_PLAN.md`](TRANSFORMATION_PLAN.md)，实施以它为准。

`docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md`、`docs/AGENTS.md` 的对应修订，由 claude 起草 PR，人类批准后合并。在此之前，V1 部分仍以 `docs/` 下的现行文件为准。

| 文件 | 内容 | 与仓库现行文件的关系 |
|---|---|---|
| [`TRANSFORMATION_PLAN.md`](TRANSFORMATION_PLAN.md) | **改造方案（定稿）**：决策记录、改造范围、目标架构、模块设计、数据模型、部署、安全、测试与验收、实施计划（OC-0 ~ OC-7）、规范修订清单 | 新增，**已采纳** |
| [`OPENCLAW_PLAN.md`](OPENCLAW_PLAN.md) | 论证记录（第 2 版）：多个 OpenClaw 分布在不同服务器，内网、外网两种接入，都由 OpenClaw 主动连回工作台；密钥集中在工作台；工作台内置基于 OpenIM 的管理员端 IM（单聊、拉群、置顶、免打扰、分组）；MCP 工具与审批，附上游源码核对和备选方案 | 新增，定稿的依据 |
| [`REVIEW.md`](REVIEW.md) | claude 对资料包的评审：问题、业内通常的处理方式、需要人类决定的事项 | 新增 |
| [`KNOWLEDGE_PLAN.md`](KNOWLEDGE_PLAN.md) | 知识库改造方案（2026-11-03，T-0096）：笔记编辑器、知识图谱、公共 / 项目 / 智能体三类知识库、智能体经 MCP 直接读写删改（不审核，用版本、回收站、密钥拦截、自动暂停保护）、项目认知对齐与自我提升；附效果图链接 | 新增，**已采纳并完成**（T-0097 ~ T-0100）；2026-11-04 的修订见 `TIMELINE_PLAN.md` 第 3、4 节 |
| [`TIMELINE_PLAN.md`](TIMELINE_PLAN.md) | 任务时间轴看板与知识库升级方案（2026-11-04，T-0101）：时间轴看板（执行者轨道、执行段、token 用量、按项目查看）、知识图谱按类型着色、取消“人已确认”、知识库图片（本地 / R2 / OSS）；附效果图链接 | 新增，**已采纳** |
| [`AGENT_OPS_PLAN.md`](AGENT_OPS_PLAN.md) | 智能体能力补齐与 OpenClaw 服务器运维升级方案（2026-11-04，T-0109）：项目成员与项目群、任务自由接取、智能体建项目、日历待办、服务器状态、远程运维动作 | 新增，**已采纳并完成**（T-0110 ~ T-0115） |
| [`AGENT_MESSAGING_PLAN.md`](AGENT_MESSAGING_PLAN.md) | 智能体主动发消息与项目协作升级方案（2026-11-04，T-0116）：经工作台 MCP 工具发文字 / 图片 / 文件 / 视频、拒收时让智能体知道、会话目录（在哪些群、能私聊谁）、发件箱与重试、定时消息与唤醒；项目与任务的 Issues（智能体之间对接）；项目与任务的 README 导览 | 新增，**已采纳**（T-0117 ~ T-0126） |
| [`MAIL_PLAN.md`](MAIL_PLAN.md) | Gmail 邮件收发方案（2026-11-06，T-0127）：Gmail API + OAuth 接入、管理员在工作台收发邮件、智能体经 MCP 查询（默认可以）与发送（管理员按实例打开，例外七）、收件人范围与限额 | 新增，**已采纳**（T-0128 ~ T-0131） |
| [`RUNNER_PLAN.md`](RUNNER_PLAN.md) | 运行服务器方案（2026-11-06，T-0134）：单独一台高配服务器给所有智能体跑容器；模板 / 环境 / 运行三层，一次性 / 会话 / 常驻三类容器，空闲分档回收、按重建耗时打分、删除条件、归档后按配置记录快速重建；runner-agent、MCP 工具与管理页面 | 新增，**待人类批准**（例外八与 D1 ~ D12） |
| [`package/README_V2.md`](package/README_V2.md) | 资料包说明 | 新增 |
| [`package/AGENT_ARCHITECTURE_V2.md`](package/AGENT_ARCHITECTURE_V2.md) | Agent Control Plane 架构增补规格（V2 的主体） | 新增 |
| [`package/AGENT_TASKS_V2.md`](package/AGENT_TASKS_V2.md) | V2-S0 ~ V2-S7 任务清单 | 新增 |
| [`package/CODEX_V2_START.md`](package/CODEX_V2_START.md) | 给 Codex 的 V2 启动指令 | 新增 |
| [`package/AGENTS.md`](package/AGENTS.md) | 项目规则 + “V2 Agent Control Plane 补充强制规则” | 只有 `docs/AGENTS.md` 第 8 节的项目规则和 V2 补充规则，**没有**第 1–7 节协作协议和第 9 节；不能直接替换 `docs/AGENTS.md` |
| [`package/DEVELOPMENT_SPEC.md`](package/DEVELOPMENT_SPEC.md) | 开发规范 | 与现行 `docs/DEVELOPMENT_SPEC.md` **逐字节相同**；架构增补第 14 节要求的修订还没有做 |
| [`package/TASKS.md`](package/TASKS.md) | Sprint 0–6 任务清单 | 是 V1 最初未勾选的版本；现行 `docs/TASKS.md` 已勾选 35/38 项 |
| [`package/CODEX_START.md`](package/CODEX_START.md) | V1 原版启动提示 | 是最初版本；现行 `docs/CODEX_START.md` 已按 D-0003 调整过 |

> 注意：`package/AGENTS.md` 只是存档。Codex 会读取工作目录路径上的 `AGENTS.md`，恢复 Codex 后不要把它的工作目录设在 `docs/v2/package/` 下。
