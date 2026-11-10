# ADR：中转站管理与 OpenClaw 模型连接（T-0200 ~ T-0208）

- 状态：accepted（实现见 T-0200 ~ T-0208；方案包 `docs/v2/model-connections/`）
- 日期：2026-11-09
- 锁定版本：OpenClaw `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`

## 1. 决定

1. 新增“中转站管理”（`/settings/model-connections`）：连接 = 协议 + base URL + API Key + 明确的模型集合。
   Key 仍存在 `agent_secrets` / `agent_secret_versions`（同一套加密与轮换规则），连接只记 `credential_secret_id`，
   任何接口都不回显 Key、密文、`credential_ref` 或内部 secret_id。旧密钥库的模型 Key 原地纳管（不复制密文、不换 ID）。
2. 每个 OpenClaw 实例一个主绑定（连接 + 模型）。绑定是**目标**，实例确认后才是**已应用**；两者分开存，失败、未知都不显示已应用。
3. 不加推理网关：OpenClaw 直连连接的 base URL；Key 通过 exec SecretRef 在需要时向工作台取（`mc_<连接 ID>`）。
4. 配置下发用**实例拉取**：接入包里的 `bin/model-config.mjs` 由宿主机 crontab 每分钟在容器里运行，
   取 `GET /agent-api/v1/model-config`，目标变了才用 OpenClaw 自己的 `config patch` 校验并写入（热加载，不重启），
   再 `secrets reload`，最后 `POST /agent-api/v1/model-config/applied` 回报。工作台不在 HTTP 请求里跑 SSH。
5. 只开放核验过、并在真实 OpenClaw 上跑通的协议（见第 3 节）；其他协议不出现在 `capabilities` 里，选了返回 422。

## 2. OpenClaw 2026.9.36 的核验结果

核验方式：离线读镜像里的代码与文档，并在 `--network none` 的容器里运行真实 CLI 与网关，
用容器内的抓包服务记录 OpenClaw 发出的每个请求（Key 是测试用的假值）。原始输出保存在核验记录
`docs/verification/T-0208-model-connections.md` 第 2 节列出的位置。

### 2.1 provider 的 `api` 取值

镜像里共 12 个（`/app/dist/model-data-*.mjs` 的 `MODEL_DATA_APIS`，schema 在 `zod-schema.core-*.mjs` 的 `ModelApiSchema`）：
`openai-completions`、`openai-responses`、`openai-chatgpt-responses`、`anthropic-messages`、`google-generative-ai`、
`google-interactions`、`google-vertex`、`github-copilot`、`bedrock-converse-stream`、`ollama`、`pi-messages`、
`azure-openai-responses`。旧值 `openai-codex-responses` 会被拒绝。

| 工作台协议 | OpenClaw `api` | 请求地址（实测） | Key 放在 |
|---|---|---|---|
| `openai_compatible` | `openai-completions` | `POST <baseUrl>/chat/completions`（`https://api.openai.com/v1` → `/v1/chat/completions`） | `Authorization: Bearer <key>` |
| `anthropic_native` | `anthropic-messages` | baseUrl 以 `/v1` 结尾时 `<baseUrl>/messages`，否则 `<baseUrl>/v1/messages` | `x-api-key`，另带 `anthropic-version: 2023-06-01` |
| `gemini_native` | `google-generative-ai` | `POST <baseUrl>/models/<id>:streamGenerateContent?alt=sse`（baseUrl 已含版本路径） | `x-goog-api-key` |

依据：`@openclaw/ai` 的 `openai-completions-transport.ts`（OpenAI SDK 7.20.0：`post('/chat/completions')`，
`new URL(baseURL + path)`，`Authorization: Bearer`）；`anthropic-transport-stream.ts` 第 143 ~ 146、299 ~ 300 行；
`google-*.mjs` 第 40 ~ 41 行（`httpOptions.baseUrl = model.baseUrl; apiVersion = ""`）与 `gemini-auth-*.mjs`。
Gemini 官方主机不带版本路径时 OpenClaw 会补 `/v1beta`，非官方主机原样使用，所以 Gemini 的 base URL 一律写明 `/v1beta`。

### 2.2 provider 键与模型条目

- provider 键在 schema 里没有字符限制，运行时会转成小写；**含 `/` 的键能通过校验但运行时找不到模型**。
  所以受管 provider 的键只用小写字母和数字：`mwc` + 连接 UUID 的前 20 个十六进制字符（`model_connections.provider_key`）。
  镜像里的内置 provider（`openai`、`anthropic`、`google` …）和插件声明的 28 个 provider id 都不以 `mw` 开头，不冲突。
- 主模型引用按**第一个** `/` 拆分，模型 ID 本身可以含 `/`（实测 `fake/org/sub-model` 发出 `"model":"org/sub-model"`）。
- 自定义 provider 必须有 `baseUrl` 与 `models` 数组；`models[]` 每项必须有 `id` 与 `name`（`.strict()`，未知键报错）。
- `agents.defaults.model` 是字符串或 `{ primary, fallbacks }`；主模型不在 `models[]` 里也能用，但工作台始终把它写进去。
- `models.mode` 默认 `merge`：保留内置 provider，叠加自定义的。

### 2.3 热加载与 Key 刷新

- `config-reload-plan-*.mjs`：`models`、`agents.defaults.model` 前缀是 `hot`（不重启）；`secrets` 是 `none`；
  `gateway`、`discovery` 要重启；未列出的路径默认重启。`gateway.reload.mode` 默认 `hybrid`。
- 实测：改 provider（新键、新端口、新 SecretRef id）与主模型后约 0.2 秒日志出现 `config hot reload applied`，
  下一轮对话就发到新地址、带新 Key；同一键改 `baseUrl` / `api` 也热加载。
- 无效配置：`config reload skipped (invalid config)`，网关继续用上一份有效配置。
- **同一个 SecretRef id 换了值（更换 Key）只有 `secrets reload` 才会重新取**；热加载对未变的引用复用缓存值。
  `secrets reload` 只重新解析密钥，不重新读配置文件。

### 2.4 校验与写入命令

- `openclaw config patch --stdin`：一次校验写入，对象递归合并、`null` 删除；网关运行时可以用，写完由网关热加载。
- `--dry-run --json` 只校验（schema 与 SecretRef 能否取到，exec 引用要加 `--allow-exec` 才会实际调用取密钥程序），
  输出 `{"ok": true|false, "checks": {"schema": …, "resolvability": …}}`。
- `openclaw config validate [--json]` 也可以，但要求 exec 取密钥程序的属主是当前用户（uid 1000）。
- 镜像的入口在启动时运行 `doctor`，会往 `openclaw.json` 里补 `agents.entries.main`、`meta`、`skills.entries`、`wizard` 等；
  所以不能整份覆盖配置文件，只能按路径打补丁（`model-config.mjs` 只动受管 provider 与 `agents.defaults.model`）。

### 2.5 其他

- 自定义 provider 没有观察到模型列表请求（只有插件提供的 provider 才做发现）；模型列表由工作台同步，见第 4 节。
- base URL 的主机解析到回环地址时 OpenClaw 拒绝请求（`blocked URL fetch … private/internal/special-use IP`），
  `request.allowPrivateNetwork: true` 可放开；工作台不替用户打开它。验收用的假模型服务在 Docker 网络里（私有地址）照常可用。
- 启动时会尝试拉取 `https://catalog.openclaw.ai/models/v{1,2}/catalog.json`（`models.catalogRefresh`），离线时只记一行警告。

## 3. 已开放的协议与官方预设

| 协议 | 状态 | 依据 |
|---|---|---|
| `openai_compatible` | 开放 | 第 2.1 节实测请求；真实 OpenClaw 端到端验收（agents 场景 27） |
| `anthropic_native` | 见核验记录第 3 节 | 请求格式已实测；只有工作台侧的模型同步与检测核验完成、并在真实 OpenClaw 上跑通后才开放 |
| `gemini_native` | 见核验记录第 3 节 | 同上 |

官方预设只填经核验的协议与 base URL，不代填 Key；官方预设配第三方主机返回 422 `MODEL_OFFICIAL_HOST_MISMATCH`。
OpenAI：`https://api.openai.com/v1`（https://platform.openai.com/docs/api-reference/introduction）。

## 4. 模型同步与检测

见 `app/integrations/model_providers.py` 的文件头与核验记录第 3 节：每个协议的模型列表、推理检测请求格式、分页、
错误归类与官方文档地址。只经工作台的出站策略访问（固定 IP、超时、不跟随跨站跳转），不保存远端原始响应。

## 5. 配置下发与状态

```
owner 保存绑定（目标修订号 +1，排应用任务 queued）
  → 实例 model-config.mjs 取配置（任务 running）
  → config patch --dry-run --allow-exec（schema + Key 可取）→ config patch → 热加载 → secrets reload
  → 回报 applied / failed(<固定原因>) / manual_required
```

- `apply_key = <目标修订号>.<任务尝试次数>`：与上次应用的不同才动手；“重新应用”让尝试次数加 1。
- 只认当前目标修订号的回报：迟到的旧回报 409 `MODEL_BINDING_CONFLICT`，不改任何状态。
- 排队或执行中超过 10 分钟没有结果显示 `unknown`（不写库，也不当失败）；再次应用重新排队。
- 切换未确认时，旧的已应用连接与新目标都能取 Key；确认后旧连接退休。解除绑定、停用连接、撤销授权立即拒绝后续取用
  （已在 OpenClaw 进程里的 Key 不保证立即失效）。
- 更换连接的 Key：以它为目标的绑定修订号加 1，实例一分钟内重新应用并 `secrets reload`，不等每 10 分钟的定时任务。
- 新部署必须选主模型：实例、连接授权、绑定、应用任务在同一个事务里建立；接入包直接写入受管 provider。
  没有绑定的旧实例保留接入包里的旧 `openai` 段（`openai_api_key`），在实例页设置主模型后由 `model-config.mjs`
  换成受管 provider 并删掉这段模板（只删与模板完全相同的那一段）；用户自己加的 provider、MCP、IM 渠道、工具限制都不动。

## 6. 不做的事

- 不加推理网关、不做备用链 / 负载均衡 / 自动择价；
- 不迁移 GitHub PAT、Gmail OAuth、服务器密码、新闻早报的模型凭据；
- 不放宽 OpenClaw 的工具限制（`tools.exec.security = deny` 不变；SecretRef 的受控 exec 不等于允许智能体 exec）；
- 不替用户打开 `request.allowPrivateNetwork`。

## 7. 为什么不做成插拔式模块

人类 2026-11-08 要求新功能按插拔式模块做（`docs/modules/README.md`）。中转站管理不按模块做，原因：
它替换的是 OpenClaw 智能体本身的模型配置——部署（新部署必须选主模型）、接入包（受管 provider）、取密钥程序（`mc_` 引用）、
Runner 与旧取密钥接口的启停校验都依赖它；“停用模块”会让已绑定的实例取不到 Key、新部署无法完成。
所以它和智能体、密钥库一样属于核心（路由登记在 `app/api/v1/__init__.py`，页面替换原来的“密钥库”入口），不提供停用开关。
