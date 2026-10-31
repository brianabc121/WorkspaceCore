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
| [`package/README_V2.md`](package/README_V2.md) | 资料包说明 | 新增 |
| [`package/AGENT_ARCHITECTURE_V2.md`](package/AGENT_ARCHITECTURE_V2.md) | Agent Control Plane 架构增补规格（V2 的主体） | 新增 |
| [`package/AGENT_TASKS_V2.md`](package/AGENT_TASKS_V2.md) | V2-S0 ~ V2-S7 任务清单 | 新增 |
| [`package/CODEX_V2_START.md`](package/CODEX_V2_START.md) | 给 Codex 的 V2 启动指令 | 新增 |
| [`package/AGENTS.md`](package/AGENTS.md) | 项目规则 + “V2 Agent Control Plane 补充强制规则” | 只有 `docs/AGENTS.md` 第 8 节的项目规则和 V2 补充规则，**没有**第 1–7 节协作协议和第 9 节；不能直接替换 `docs/AGENTS.md` |
| [`package/DEVELOPMENT_SPEC.md`](package/DEVELOPMENT_SPEC.md) | 开发规范 | 与现行 `docs/DEVELOPMENT_SPEC.md` **逐字节相同**；架构增补第 14 节要求的修订还没有做 |
| [`package/TASKS.md`](package/TASKS.md) | Sprint 0–6 任务清单 | 是 V1 最初未勾选的版本；现行 `docs/TASKS.md` 已勾选 35/38 项 |
| [`package/CODEX_START.md`](package/CODEX_START.md) | V1 原版启动提示 | 是最初版本；现行 `docs/CODEX_START.md` 已按 D-0003 调整过 |

> 注意：`package/AGENTS.md` 只是存档。Codex 会读取工作目录路径上的 `AGENTS.md`，恢复 Codex 后不要把它的工作目录设在 `docs/v2/package/` 下。
