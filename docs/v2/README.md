# My Workspace V2.0 设计资料（存档）

人类在 2026-10-31 提供了 V2.0 设计资料包（`MyWorkspace_V2_Codex_开发升级包.zip`，8 个 Markdown 文件，
sha256 `2c0f41d55f9369c95a0aa83c64f05e905c9a48df353bd677765a6f3d4e3386a2`）。本目录原样保存解压结果，并附上 claude 的评审。

**状态：方案草稿，尚未采纳。** 2026-10-31 人类决定用 OpenClaw 接替 AI 方案，并说明 OpenClaw 将运行在另一台服务器上。据此重写的优化方案见 [`OPENCLAW_PLAN.md`](OPENCLAW_PLAN.md)。之后人类又提出三项调整：多实例与内外网接入、密钥集中、OpenIM 内置 IM；方案已按调整更新为第 2 版，等人类批准。
这些文件还没有并入 `docs/DEVELOPMENT_SPEC.md`、`docs/AGENTS.md`、`docs/TASKS.md`。
在人类决定采纳并修订之前，开发仍以 `docs/` 下的现行文件和 `.collab/` 为准（`docs/AGENTS.md` 第 1 节）。

| 文件 | 内容 | 与仓库现行文件的关系 |
|---|---|---|
| [`OPENCLAW_PLAN.md`](OPENCLAW_PLAN.md) | **优化方案（第 2 版）**：多个 OpenClaw 分布在不同服务器，内网、外网两种接入，都由 OpenClaw 主动连回工作台；密钥集中在工作台；工作台内置基于 OpenIM 的管理员端 IM（单聊、拉群、置顶、免打扰、分组）；MCP 工具与审批 | 新增，替换资料包中 AI 与 Agent 的部分（待批准） |
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
