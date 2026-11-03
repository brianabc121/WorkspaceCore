# 2026-11-03 知识库 K-0 核验：前端库版本与 OpenClaw 2026.9.36 的行为（T-0097）

## 1. 背景

`docs/v2/KNOWLEDGE_PLAN.md` 第 9 节要求，实施前先核验两件事：
- 要用的前端库：版本、许可证；
- OpenClaw 锁定版本里与知识库相关的行为。

依据 `docs/AGENTS.md` 第 8 节第 3 条和 V2 补充规则：只认锁定版本的源码、随版本发布的文档或运行实例，不按概念名称猜。

## 2. 前端库（npm 注册表，2026-11-03 查询）

| 包 | 选用版本 | 发布时间 | 许可证 | 用途 |
|---|---|---|---|---|
| `codemirror` | 6.0.2 | 2025-06-19 | MIT | 编辑器的基础配置（basicSetup） |
| `@codemirror/view` | 6.43.15 | 2026-10-19 | MIT | 编辑视图 |
| `@codemirror/state` | 6.7.6 | 2026-09-22 | MIT | 编辑状态 |
| `@codemirror/commands` | 6.11.1 | 2026-09-15 | MIT | 快捷键与命令 |
| `@codemirror/autocomplete` | 6.20.4 | 2026-10-19 | MIT | `[[` 与 `#` 补全 |
| `@codemirror/language` | 6.13.1 | 2026-10-07 | MIT | 语法高亮 |
| `@codemirror/lang-markdown` | 6.5.2 | 2026-08-04 | MIT | Markdown 语法 |
| `@lezer/highlight` | 1.2.5 | 2026-09-28 | MIT | 高亮样式标签 |
| `markdown-it` | 15.0.3 | 2026-10-16 | MIT | Markdown 渲染（自带类型定义 `dist/markdown-it.d.mts`） |
| `dompurify` | 3.4.17 | 2026-10-12 | MPL-2.0 OR Apache-2.0 | 渲染结果消毒 |
| `d3-force` | **3.0.0** | 2021-06-05 | ISC | 图谱的力导向布局 |
| `@types/d3-force` | 3.0.10 | — | MIT | 类型定义 |
| `node-diff3` | 3.2.1 | 2026-06-03 | MIT | 编辑器里的三方合并 |

- `d3-force` 的最新版 3.0.1 是当天（2026-11-03）刚发布的，所以选用发布已久的 3.0.0；两者的接口一样。
- 安装时写成精确版本（不带 `^`），与现有依赖一致；`npm audit` 由 CI 的 deps 任务检查。
- 后端的版本差异（变更记录、版本对比）用 Python 标准库 `difflib`，不加依赖。

## 3. OpenClaw 2026.9.36

**来源**：从 GitHub `openclaw/openclaw` 的标签 `v2026.9.36` 克隆（提交 `70c40e4`），只读源码与 `docs/`；镜像就是 `docs/versions.md` 里锁定的版本。

### 3.1 MCP 服务的 `instructions` 不交给模型

- 源码里没有调用 MCP SDK 的 `getInstructions()`，也没有在别处读取初始化结果里的 `instructions`。
- 能看到的只有 `getServerCapabilities()`，在 `src/agents/agent-bundle-mcp-runtime.ts`。

结论：写入准则不能靠服务说明传给模型。

### 3.2 工具描述会清洗，并截到 1200 个字符

- `src/agents/mcp-metadata.ts` 的 `sanitizeMcpMetadataText`：
  - 把“ignore / disregard previous instructions”一类的话替换掉；
  - 超过 `MCP_METADATA_TEXT_LIMIT = 1_200` 个字符就截断。
- 在 `src/agents/agent-bundle-mcp-runtime.ts` 里，工具描述交给模型前都经过这个函数。

结论：`knowledge_*` 的工具描述每条不超过 1200 个字符，并加测试检查。

### 3.3 智能体不能自己建定时任务；运维用 CLI 建

- 接入包的 `openclaw.json` 在 `tools.deny` 里禁用了 `group:automation`。
- 定时任务的建法见随版本文档 `docs/automation/cron-jobs/payloads.md`、`managing-jobs.md`：
  - 命令是 `openclaw automations add --name <名字> --cron <表达式> --tz <时区> --session isolated --message <提示词>`；
  - 创建、修改需要 `operator.admin`。

结论：
- 每晚复盘任务由 SSH 部署在安装、升级后，用运维身份建好；先 `openclaw automations list` 查同名任务，有就跳过；
- 手工部署照 CHECKLIST 执行同一条命令；
- 实际行为在 CI 的真实 OpenClaw 上验证（T-0100）。

### 3.4 IM 渠道只放行管理员

接入包的 `channels.openim.accounts.default.inboundWhitelist` 只有 `${MYWS_ADMIN_IM_ID}`。工作台通知账号（`ws_notice`）发给智能体的私聊会被渠道忽略。

结论：“提醒它读最新简报”由管理员点按钮、以自己的 IM 账号发出；工作台不自动替管理员发消息。

### 3.5 OpenClaw 自己的记忆

来自随版本文档 `docs/concepts/memory.md`、`memory-architecture.md`、`docs/plugins/memory-wiki.md`：
- 记忆是工作区里的 Markdown 文件，由 dreaming 定时整理；
- 来源分 owner / agent / untrusted / system；
- 自带的 `memory-wiki` 插件能编出兼容 Obsidian 的 wiki。

这些只影响 OpenClaw 本机的记忆。工作台不读也不同步；`memory-wiki` 属于“其他插件”，按实例检查清单不安装。

## 4. 影响

`docs/v2/KNOWLEDGE_PLAN.md` 的 3.9、3.10、第 7 节已按以上结果改写。

## 5. 补充：定时任务的实测（T-0100，2026-11-03）

在本机的 `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f` 上实测，修正 3.3 的做法。

- **不用先查再建**：`automations add --help` 有 `--declaration-key <key>`（“Idempotent declaration identity key”）。
  - 用同一个 key 再运行一次，输出 `"created": false`、`"updated": true`，只更新提示词与时间，不会建出第二个任务。
  - 所以 `setup-reflection.sh` 直接用 `--declaration-key myworkspace-knowledge-reflection`，不再先 `automations list` 查同名任务。
  - SSH 部署据输出里的 `"created": false` 显示“已有，已更新”。
- **建任务用的参数**：`--cron <表达式> --tz <IANA 时区> --session isolated --no-deliver --message <提示词>`。
  - 输出里 `sessionTarget` 是 `isolated`，`delivery.mode` 是 `none`；
  - 结果不投递到任何渠道。
- **`automations list --json` 的结构**：`{jobs: [...], total, ...}`。每个 job 有 `id`、`declarationKey`、`schedule`、`sessionTarget`、`payload`：
  - `schedule` 是 `{kind: "cron", expr, tz}`；
  - `payload` 是 `{kind: "agentTurn", message, toolsAllow}`。
  - 默认还有一个内置的 `heartbeat:main` 任务，验收按 `declarationKey` 找我们的任务。
- **工具范围**：新建的任务默认 `payload.toolsAllow: ["*"]`、`scheduledToolPolicy.mode: "trusted"`。
  - 这不放宽接入包 `openclaw.json` 的工具限制（`tools.deny` 等对所有运行都生效）。
  - CI 的 agents 任务（`tests/e2e/agents_acceptance.py` 场景 15）用 `automations run <id> --wait` 立即运行一次复盘，检查模型拿到的工具只有 IM 发消息与工作台工具。
  - `--tools <list>` 可以进一步限定，但它和 Tool Search 暴露的 MCP 工具怎么配合没有核验，暂不使用（TODO：核验后再收紧）。
- **权限**：CLI 在容器里用接入包的网关令牌（`OPENCLAW_GATEWAY_TOKEN`）连本机网关，和现有的 `secrets reload` 定时任务一样。
- **运行时模型收到什么**：`automations run <id> --wait` 立即运行一次（调试用），本机加假模型实测：
  - 新的独立会话（`sessionKey` 是 `agent:main:cron:<id>:run:<会话>`），约 10 秒跑完，结果 `deliveryStatus: not-requested`；
  - 用户消息是 `[cron:<id> <名字>] <提示词>`，后面接一段 OpenClaw 自带的英文前言（任务指令优先；任务不再需要时“在工具允许时删除它”，接入包禁用了定时任务工具，智能体删不了）。
  - CI 的场景 15 按这个格式认出复盘请求。
