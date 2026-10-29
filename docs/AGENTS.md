# AGENTS.md — 所有 AI 代理的工作约定

本仓库由两个 AI 代理协作开发 My Workspace，人类（仓库所有者）负责派活、裁决和合并：

| 身份 | 是谁 | 职责 |
|---|---|---|
| `claude` | Claude Code | 做 owner 是 claude 的任务 |
| `codex` | OpenAI Codex | 做 owner 是 codex 的任务 |
| `human` | 仓库所有者 | 派活、裁决分歧、审批决策、**唯一能合并 PR 的人** |

**你每次会话都没有之前的记忆——仓库里的 `.collab/` 就是你和对方共同的记忆。**
你是 Codex 就用身份 `codex`；你是 Claude Code 就用 `claude`（CLAUDE.md 里也会写明）。
所有 `scripts/collab.py` 命令都要带 `--as <你的身份>`。

- 第 1–7 节：两个代理之间怎么分工和交接（协作协议）。
- **第 8 节：项目本身的工程规则，由人类制定**；与 1–7 节冲突时以第 8 节为准，并在任务日志里指出冲突。
- 人类给的操作说明和设计原因见 `.collab/README.md`。
- 本文件位于 `docs/AGENTS.md`；仓库根目录的 `AGENTS.md` 是指向它的链接（Codex 只自动读取根目录的那份），不要删除。

## 1. 事实来源

| 内容 | 位置 | 规则 |
|---|---|---|
| 需求与路线图 | `docs/DEVELOPMENT_SPEC.md`、`docs/TASKS.md` | 人类制定。`TASKS.md` 的条目由该 Sprint 的验收任务附测试日志统一勾选（避免并行 PR 在相邻行上冲突），其他任务不要改它 |
| 目录归属 | `.collab/decisions/D-0003-module-ownership.md` | 每个目录长期归哪个代理；接口契约见 `docs/decisions/` |
| 任务与进度 | `.collab/tasks/T-xxxx*.md` | 谁在做、做到哪了。头部 `owner`/`status` 是唯一的状态；正文末尾“进度日志”只追加。任务标题写明对应的 `TASKS.md` 编号（如 S0-01） |
| 交接 | `.collab/handoffs/*.md` | 一次交接一个文件，**提交后不再修改** |
| 技术决策（ADR） | `docs/decisions/` | 架构选择、第三方 API / 版本核验结果（附来源链接），按项目规范要求写 |
| 协作决策 | `.collab/decisions/D-xxxx*.md` | 只记录协作流程本身的决策；改流程前先读，不悄悄推翻 |

- 以**主干（main）**上的内容为准。一个 PR 对这些文件的修改，表示“**这个 PR 合并之后**”的状态。
- 交接随 PR 合并才生效。接收方总是从最新的 main 开工（除非人类明确指定了别的分支）。
- 不要用记忆、聊天记录或 GitHub Issue 代替这些文件。带 `collab-board` / `collab-handoff` 标签的 issue
  是 CI 根据这些文件自动生成的镜像和提醒，不要手动编辑或关闭。

## 2. 球权（owner）

- 每个任务任何时刻只有一个 `owner`，含义是“**下一步该谁行动**”：`claude` / `codex` / `human` / `unassigned`。
- `status`：`todo` → `in_progress` →（`handoff` | `blocked`）→ `done`；放弃用 `cancelled`。
  - `handoff`：球已传给 owner，等 owner 接手。
  - `blocked`：卡住了；上报人类时 owner 为 `human`。
- **只做 owner 是你的任务。** 别人的任务：不改它 `paths` 范围内的代码，不改它的状态；可以用 `collab.py log` 在它的日志里追加意见。
- 球权转移**只能**通过交接单（`collab.py handoff`）；只有人类可以直接改 owner。

## 3. 开工

1. 从最新的 main 开始（Claude 云端会话会给你一个专属分支，在它上面工作即可）。
2. 运行 `python3 scripts/collab.py inbox --as <你>`。
3. 处理顺序：人类本次对话里的明确指令 > 待你接手的交接 > 你进行中的任务 > 已分配给你的 todo。
   未分配的任务只有人类指派或同意后才能认领。
4. 接手交接：完整阅读交接单、任务文件和其中引用的决策 → 先跑交接单第 4 节的验证命令确认现状 →
   `python3 scripts/collab.py start T-xxxx --as <你>`。
   交接单不清楚或验证失败时**不要猜**：在任务日志里写明问题；无法自行解决就
   `handoff --to human --type escalate`，或者用 `rework` 交还给发出方。
5. 人类在本次对话里明确把某个任务指派给你（包括处理完上报后交给你）：先记录这次指派
   `python3 scripts/collab.py set T-xxxx --as human --owner <你> -m "人类在对话中指派：<要点>"`，再 `start`。
   这是代理唯一可以用 `--as human` 执行的操作，而且必须有人类的明确指派。
6. 人类的指令与球权冲突时（例如要你改对方 `in_progress` 的东西），先指出冲突一次；人类坚持就照做，并在日志里记录。

## 4. 干活中

- 只改任务 `paths` 声明范围内的文件。必须越界时，先确认没有碰到对方进行中任务的范围
  （`inbox` 第 6 节、`check` 的重叠警告），并在日志里写明原因。
- 提交信息按项目规范（`docs/DEVELOPMENT_SPEC.md` 第 14 节）写，末尾带任务号：`feat(api): 增加 /healthz（T-0004）`。
- 保持 main 随时可用：未完成的工作也要能构建、测试通过（模块先不接入、用开关等）。
- 技术和架构决策写进 `docs/decisions/`。协作流程本身的决策用 `collab.py decision` 写进 `.collab/decisions/`：
  只影响你自己任务范围的，可以直接写 `status: accepted`；涉及协作协议的，写 `proposed`，由人类改成 `accepted`。
  对已 accepted 的决策有异议：写新的提案并 escalate，不要直接改代码推翻它。
- 发现对方代码的问题：不要顺手改。在对方任务日志里 `log`，或在 PR 上评论；
  对方任务已 done 的，新建任务或用 `rework` 交还给原作者（由人类决定）。

## 5. 收工（每次会话必须以下面三种之一结束）

- **A. 完成**：确认验收标准都已勾选 → `collab.py set T-xxxx --as <你> --status done -m "..."`
- **B. 交出**：`collab.py handoff T-xxxx --as <你> --to <对方|human> --type <continue|rework|escalate> -m "一句话摘要"`，
  然后**立刻填完**生成的交接单，不能留下任何“（待填写”。交接单要写到对方不问你任何问题就能接着干。
- **C. 下次继续**：保持 `in_progress`，`collab.py log T-xxxx --as <你> "做到哪了；下一步是什么"`。
  下次的你同样没有记忆，日志要写到别人也能接手的程度。

然后：

1. `python3 scripts/collab.py check` 必须通过（CI 也会跑）。
2. 代码改动和 `.collab/` 改动放在**同一个 PR**。
3. PR 标题 `[T-xxxx] 简述`，按 `.github/pull_request_template.md` 填写（它包含第 8 节要求的报告结构）。
4. 推送你自己的分支并开 PR（或按你所在环境的方式产出 PR），然后停下，等人类合并。

交接类型：

| type | 用途 |
|---|---|
| `continue` | 接力：把未完成的工作交给对方继续 |
| `rework` | 返工：对方交来的或已完成的工作有问题，交还给原作者修 |
| `escalate` | 上报：需要人类决定（只能交给 `human`） |

## 6. 红线

- 不推送到对方的分支；不 force-push 共享分支；**不合并 PR**。
- 不修改已提交的交接单；要更正就再发一份新的。
- 不改 owner 不是你的任务的状态。
- 不擅自修改协作协议本身（本文件 1–7 节、`.collab/templates/`、`scripts/collab.py` 的字段和状态集合）：
  先写 `proposed` 决策，人类批准后再改。第 9 节例外，由 codex 自己维护。
- 不悄悄重写对方的代码风格或架构；有意见写日志、PR 评论或决策提案。

## 7. 审查与小改动

- 审查在 PR 上进行：审查方在 PR 上留评论，不往作者的分支推送。作者负责回应审查意见。
  建议非琐碎的 PR 都由另一个代理审查一次（人类在 PR 下评论触发，或在对话里让代理去审）。
- 不建任务的小改动：只改错字、注释、格式这类不超过 20 行、且不碰别人占用路径的修改，
  PR 标题用 `[chore] 简述`。其余改动都要先有任务。

## 8. 项目规则：My Workspace（由人类制定，适用于所有代理）

> 以下内容是人类写的项目工程规则，原文按 Codex 的口吻写成；Claude 实施代码时同样遵守。
> 文中的 `DEVELOPMENT_SPEC.md`、`TASKS.md` 位于 `docs/` 目录。

你正在实现 `DEVELOPMENT_SPEC.md` 所描述的混合型全能个人工作台。

### 工作规则
1. 每轮先阅读 `DEVELOPMENT_SPEC.md`、`TASKS.md`、相关现有源码与变更记录。若目录尚无源码，先做 Sprint 0。
2. 仅实施当前选定 Sprint 的下一个未完成任务，不要跨 Sprint 一次生成全部系统；先写短计划，再修改代码。
3. 禁止发明第三方 API、Webhook 格式和版本。必须核验运行实例或当前锁定 release 的官方 OpenAPI/文档；不可验证则实现明确的接口桩和 TODO，不标为完成。
4. 任何数据表结构变化必须提供 Alembic 迁移和测试；不要直接更改第三方产品数据库。
5. 后端模块化单体，使用 FastAPI/Pydantic 2/SQLAlchemy 2；前端 Vue 3/TS，组件复用，API 强类型。
6. Token、Secret、Cookie、Webhook payload 必须脱敏；不能提交明文密钥、真实用户信息或生产配置。
7. 外部 HTTP 必须有 timeout、错误归类、重试限制；写操作具有幂等和未知完成状态处理。
8. 所有数据读取和写入均做 workspace scope + role 验证；不能只在前端隐藏按钮。
9. 对任何 AI/自动化有副作用的操作强制工具策略校验；Critical 类操作需要显式审批。
10. 完成前运行相关 lint/typecheck/tests/build/migrations；不能运行的项目注明原因、命令和实际输出，不得虚构通过结果。

### 提交格式
每次响应按以下结构总结：
- 当前 Sprint / 已完成任务
- 变更文件
- 实现决策及偏差
- 实际执行的命令和结果
- 安全/数据迁移影响
- 遗留问题与下一步

### 最高优先级架构约束
- Vikunja 是任务主源；AFFiNE V1 仅深链接与收藏，不猜测内容读写 API。
- 工作台 Owns User/Workspace/Widget/Calendar/Mapping/Notification/Audit。
- 第三方服务故障可降级；Webhook Inbox + 周期对账双保险。
- 核心 Compose 优先，n8n/AFFiNE/Monitoring 可选部署，生产禁止浮动 latest 镜像。

### 常用命令（`docs/DEVELOPMENT_SPEC.md` 第 11.3 节；Sprint 0 建立后可用）
`make setup`、`make dev`、`make up`、`make migrate`、`make test`、`make lint`、`make backup`、`make restore-verify`、`make down`。
交接单第 4 节和 PR 的“实际执行的命令和结果”都应引用这些命令。

## 9. Codex 环境备注（由 codex 维护）

Claude 专属的说明在 CLAUDE.md；Codex 所在环境的注意事项写在这里（分支命名、能否联网、如何产出 PR 等）。

- （暂无）
