# T-0202 原生协议（Anthropic Messages、Gemini）：核验记录

中转站管理原来只开放 `openai_compatible`。本记录核验 `anthropic_native`（Anthropic Messages API）与
`gemini_native`（Gemini API，generativelanguage.googleapis.com），确认两件事都成立后才开放
（`app/domain/model_connections.py` 的 `SUPPORTED_PROTOCOLS`）：

1. 工作台自己的模型同步与连接检测（`app/integrations/model_providers.py` 的 `AnthropicAdapter`、
   `GeminiAdapter`）按官方文档发请求、按官方错误格式归类；
2. 锁定版本的真实 OpenClaw（`openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`）
   用这两种 `api` 跑通一轮对话：请求发到假模型服务、带对的鉴权头、拿到 `echo:` 回复。

日期：2026-11-10。所有 Key 都是测试用的假值（`fake-test-key-<SecretRef id>`、`fake-workbench-key-<协议>`），
不是任何真实凭据；下文只写出前缀。

## 1. 官方文档核实

### 1.1 Anthropic Messages API

| 事实 | 依据 |
|---|---|
| 鉴权：`x-api-key: <Key>`（官方 API 概览：`Authorization: Bearer` 之外，`x-api-key` 是“Legacy fallback for Authorization, still supported”）；必须带 `anthropic-version` | https://platform.claude.com/docs/en/api/overview |
| `anthropic-version: 2023-06-01` 仍是有效版本；最新 `2026-10-21` 只改了 Messages 的采样参数、引用块与 Admin API，Models 接口两个版本行为相同。OpenClaw 发的就是 `2023-06-01`，工作台与它一致 | https://platform.claude.com/docs/en/api/versioning ；claude-api 技能 `shared/api-version-migration.md` 的 Scope 一节 |
| 列模型：`GET /v1/models`，查询参数 `limit`（默认 20，1 ~ 1000）、`after_id`、`before_id`；响应 `data[]`（`type: "model"`、`id`、`display_name`、`created_at` …）、`has_more`、`first_id`、`last_id` | https://platform.claude.com/docs/en/api/models/list |
| 推理：`POST /v1/messages`，必填 `model`、`max_tokens`、`messages`；`stream` 可选；响应 `type: "message"`、`role: "assistant"`、`content[]`、`stop_reason` … | https://platform.claude.com/docs/en/api/messages/create |
| 流式事件顺序：`message_start` → 每个内容块 `content_block_start` / `content_block_delta`（`text_delta`）/ `content_block_stop` → `message_delta` → `message_stop`；可夹 `ping`；每个事件带 `event:` 名称 | https://platform.claude.com/docs/en/build-with-claude/streaming |
| 错误：`{"type": "error", "error": {"type", "message"}, "request_id"}`；400 invalid_request_error、401 authentication_error、402 billing_error、403 permission_error、404 not_found_error、413 request_too_large、429 rate_limit_error（`retry-after`）、500 api_error、504 timeout_error、529 overloaded_error | https://platform.claude.com/docs/en/api/errors |
| 模型不存在与组织无权使用都返回 404 `not_found_error`，message 以 `model: <id>` 开头 | claude-api 技能 `shared/error-codes.md` 的 404 一节 |

工作台的做法：`GET …/v1/models?limit=1000`（一次取最大页，`has_more` 为真时结果不完整、不覆盖旧快照）；
`POST …/v1/messages`，请求体固定为 `{"model", "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}], "stream": false}`，
`type == "message"` 且 `content` 是数组才算通过；529 归为 `unreachable`；404 `not_found_error` + `model:` 归为 `model_not_available`。
地址与 OpenClaw `resolveAnthropicMessagesUrl` 相同：基地址以 `/v1` 结尾直接拼，否则补 `/v1`。

### 1.2 Gemini API

`https://ai.google.dev/api/models`、`https://ai.google.dev/api/generate-content`、`https://ai.google.dev/gemini-api/docs/api-key`
在本会话的网络里不能访问（代理对 `ai.google.dev`、`docs.cloud.google.com` 返回 403）。改用同一 API 的官方机器可读描述与官方源码核实：

| 事实 | 依据 |
|---|---|
| 根地址 `https://generativelanguage.googleapis.com/`；`models.list` = `GET v1beta/models`，查询参数 `pageSize`（“If unspecified, 50 models will be returned per page. This method returns at most 1000 models per page”）、`pageToken`；响应 `ListModelsResponse`：`models[]`、`nextPageToken`（“If this field is omitted, there are no more pages”） | discovery 文档 `https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta`（revision 20261109）；`googleapis/googleapis` 的 `google/ai/generativelanguage/v1beta/model_service.proto`（`get: "/v1beta/models"`） |
| `Model.name` 格式 `models/{model}`；`supportedGenerationMethods`（例如 `generateContent`、`embedContent`） | 同上 discovery 文档的 `Model` schema |
| `models.generateContent` = `POST v1beta/{+model}:generateContent`（`model` 匹配 `^models/[^/]+$`）；`streamGenerateContent` 同形；请求体 `GenerateContentRequest`：`contents[]`（`role`、`parts[].text`）、`generationConfig.maxOutputTokens`；响应 `candidates[]`、`promptFeedback`、`usageMetadata`、`modelVersion` | discovery 文档；`generative_service.proto`（`post: "/v1beta/{model=models/*}:generateContent"`） |
| 鉴权头 `x-goog-api-key` | 镜像里官方 `@google/genai` 2.23.0（`GOOGLE_API_KEY_HEADER = 'x-goog-api-key'`）；OpenClaw `gemini-auth-*.mjs`。discovery 文档另有查询参数 `key`，工作台不用（Key 不进 URL） |
| 错误：`google.rpc.Status`（`code`、`message`、`details`）的 JSON 形式 `{"error": {"code", "message", "status", "details"}}`；HTTP 映射 400 INVALID_ARGUMENT、401 UNAUTHENTICATED、403 PERMISSION_DENIED、404 NOT_FOUND、429 RESOURCE_EXHAUSTED、503 UNAVAILABLE、504 DEADLINE_EXCEEDED | `google/rpc/status.proto`、`google/rpc/code.proto`；`@google/genai` 2.23.0 的 `throwErrorIfNotOK` 构造的也是 `{error: {message, code, status}}` |
| Key 无效：400 + `ErrorInfo.reason = "API_KEY_INVALID"`（domain `googleapis.com`） | `google/api/error_reason.proto`；Google 官方 `google-gemini/gemini-api-cli` 的契约测试 `tests/contract/error_taxonomy_test.go`（“API_KEY_INVALID on HTTP 400 is an authentication error”） |
| 限流的等待时间：`google.rpc.RetryInfo.retryDelay`，Duration 的 JSON 形式是 `"17s"` / `"3.000001s"` | `google/rpc/error_details.proto`、`google/protobuf/duration.proto` |
| 流式：`…:streamGenerateContent?alt=sse`，每个 `GenerateContentResponse` 一行 `data:` | `@google/genai` 2.23.0（`'{model}:streamGenerateContent?alt=sse'`）；OpenClaw `transport-stream-*.mjs` 第 102、554 ~ 574 行 |

工作台的做法：`GET {base}/models?pageSize=1000`（`nextPageToken` 非空时不完整）；`name` 去掉 `models/` 前缀作为 model_id；
`supportedGenerationMethods` 存在但不含 `generateContent` 的（向量模型等）不列；`{}`（proto3 JSON 省略空列表）算完整的空列表。
`POST {base}/models/<id>:generateContent`，请求体固定为 `{"contents": [{"role": "user", "parts": [{"text": "ping"}]}], "generationConfig": {"maxOutputTokens": 16}}`，
有非空 `candidates` 或有 `promptFeedback`（提示被过滤拦下，Key 与模型都可用）才算通过。基地址与模型路径与 OpenClaw 相同：
官方主机路径为空时补 `/v1beta`（`normalizeGoogleApiBaseUrl`）；`models/`、`tunedModels/` 开头的原样使用，否则去掉 `google/` 后加 `models/`（`resolveGoogleModelPath`）。

**TODO(T-0202)**：会话网络放行 `ai.google.dev` 后，对照上面三个官方页面复核一遍（字段名与 discovery 文档一致，预计无变化）。

## 2. 真实 OpenClaw 实测

### 2.1 环境

- 本会话自己的 Docker 守护进程；容器与网络都以 `mcn-` 开头，结束后全部删除（第 2.5 节）。没有动 `t76-pg`、`t76-redis` 及其他不认识的容器。
- 网络 `mcn-net` 用 `--internal`（不能出网；OpenClaw 启动时拉取 catalog 失败只记警告）。
- 假模型服务：仓库的 `tests/e2e/fake_model_server.py`（本任务扩展了 Anthropic 与 Gemini，见第 3 节），容器 `mcn-fake-model`，
  镜像 `python:3.12.15-slim-trixie`，`--user 1000:1000`，地址用容器名 `mcn-fake-model`（Docker DNS 解析到 172.28.0.2，私有地址；
  OpenClaw 对自定义 provider 的 baseUrl 来源照常放行，没有出现 MC-0 记录的回环地址拦截）。
- OpenClaw：每个协议一个网关容器（镜像默认入口 `docker-entrypoint.mjs` → `doctor` → `openclaw gateway`），
  状态目录挂到 `/home/node/.openclaw`（属主 1000、`openclaw.json` 600），取密钥程序挂到 `/opt/myworkspace/bin`。
- apiKey 用 exec SecretRef（与生产相同的 `secrets.providers.myworkspace`）；测试用的取密钥程序对每个 id 返回 `fake-test-key-<id>`。
- provider 键与生产规则一致（`mwc` + 20 个十六进制字符）：Anthropic `mwc020200000000000000a1`，Gemini `mwc020200000000000000b2`。

配置（两份只差 provider 段；网关令牌是测试用的固定值，此处略去）：

```json
{
  "gateway": {"mode": "local", "bind": "loopback", "auth": {"mode": "token", "token": "<测试值>"}},
  "secrets": {"providers": {"myworkspace": {"source": "exec", "command": "/opt/myworkspace/bin/myworkspace-secret-resolver",
                                            "passEnv": ["PATH"], "jsonOnly": true, "timeoutMs": 5000}}},
  "models": {"providers": {"mwc020200000000000000a1": {
    "baseUrl": "http://mcn-fake-model:18080", "api": "anthropic-messages",
    "apiKey": {"source": "exec", "provider": "myworkspace", "id": "mc_anthropic_e2e"},
    "models": [{"id": "fake-model", "name": "Fake model"}]}}},
  "agents": {"defaults": {"model": {"primary": "mwc020200000000000000a1/fake-model"}}},
  "session": {"dmScope": "per-channel-peer"},
  "tools": {"profile": "messaging", "fs": {"workspaceOnly": true}, "exec": {"security": "deny", "ask": "always"},
            "elevated": {"enabled": false}, "agentToAgent": {"enabled": false}}
}
```

Gemini 的 provider：`"baseUrl": "http://mcn-fake-model:18080/v1beta"`、`"api": "google-generative-ai"`、SecretRef id `mc_gemini_e2e`、
`primary` 为 `mwc020200000000000000b2/fake-model`。

### 2.2 命令

`$S` 是本会话 scratchpad 里的 `mcn-run` 目录，`$IMG` 是上面的锁定镜像（带 digest）。

```bash
docker network create --internal mcn-net                                    # exit 0
docker run -d --name mcn-fake-model --network mcn-net --user 1000:1000 \
  -e FAKE_MODEL_LOG=/work/fake_model.jsonl -v $S/fake:/work \
  --entrypoint python python:3.12.15-slim-trixie -I /work/fake_model_server.py 18080   # exit 0

# Anthropic
docker run -d --name mcn-oc-anthropic --network mcn-net \
  -v $S/state-anth:/home/node/.openclaw -v $S/bin:/opt/myworkspace/bin:ro "$IMG"        # exit 0
docker exec mcn-oc-anthropic node /app/openclaw.mjs agent --agent main -m 'say pong' --json      # exit 0（3 次）
printf '%s' '{"models":{"providers":{"mwc020200000000000000a1":{"baseUrl":"http://mcn-fake-model:18080/v1"}}}}' \
  | docker exec -i mcn-oc-anthropic node /app/openclaw.mjs config patch --stdin                  # exit 0
docker exec mcn-oc-anthropic node /app/openclaw.mjs agent --agent main -m 'say pong again' --json  # exit 0

# Gemini
docker run -d --name mcn-oc-gemini --network mcn-net \
  -v $S/state-gem:/home/node/.openclaw -v $S/bin:/opt/myworkspace/bin:ro "$IMG"         # exit 0
docker exec mcn-oc-gemini node /app/openclaw.mjs agent --agent main -m 'say pong' --json         # exit 0（2 次）
```

Anthropic 三轮时假服务还没有记 `query` / `alt` 两个日志字段（后来补上，再跑了 Gemini 第 2 轮与第 2.4 节）；两份脚本只差这两个日志字段，响应完全相同。

`agent` 命令没有加 `--local`：CLI 经 `ws://127.0.0.1:18789` 交给网关执行（网关日志里有
`⇄ res ✓ agent … runId=<同一个 runId>`，CLI 的 stderr 为空，没有出现 `--local` 时的“gateway secrets.resolve unavailable”）。

### 2.3 结果

| 轮次 | runId | `agent --json` 结果 | 假模型服务收到的请求（FAKE_MODEL_LOG） |
|---|---|---|---|
| Anthropic 1（基地址无 `/v1`） | b7c4c0aa-… | `status: ok`，`payloads[0].text = "echo: say pong"`，provider `mwc020200000000000000a1`，model `fake-model` | `POST /v1/messages`，`x-api-key: fake-test-key-…_e2e`（与 SecretRef `mc_anthropic_e2e` 取到的值一致），`anthropic-version: 2023-06-01`，无 `Authorization`，`stream: true`，`model: fake-model` |
| Anthropic 2 | e5aaba94-… | `ok`，`echo: say pong` | 同上 |
| Anthropic 3（热改基地址为 `…:18080/v1` 后） | 3e2e1a22-… | `ok`，`echo: say pong again` | 仍是 `POST /v1/messages`（没有拼出 `/v1/v1`），鉴权头同上 |
| Gemini 1 | b162abc1-… | `ok`，`echo: say pong`，provider `mwc020200000000000000b2` | `POST /v1beta/models/fake-model:streamGenerateContent`，查询参数只有 `alt=sse`，`x-goog-api-key: fake-test-key-…_e2e`（SecretRef `mc_gemini_e2e`），无 `Authorization`、无 `x-api-key` |
| Gemini 2 | a901b30c-… | `ok`，`echo: say pong again` | 同上 |

关键日志（网关 stdout，节选）：

```
[gateway] agent model: mwc020200000000000000a1/fake-model (thinking=off, fast=off)
[gateway] ready
[reload] config change detected; evaluating reload (models.providers.mwc020200000000000000a1.baseUrl)
[reload] config hot reload applied (models.providers.mwc020200000000000000a1.baseUrl)
[gateway] agent model: mwc020200000000000000b2/fake-model (thinking=off, fast=off)
[gateway] ready
```

`config patch` 输出：`Applied 1 config update(s). Change will apply without restarting the gateway.`

两个网关的日志里都没有 `blocked URL fetch`、`model-fetch … error`。

### 2.4 工作台适配器对同一假服务的真实 HTTP 请求

在本机用工作台代码（`ConnectorHttp` + 出站策略，`INTEGRATION_ALLOWED_HOSTS=172.28.0.2`，生产传输层，不是 MockTransport）访问假服务：

```
anthropic_native list passed None True ('fake-model',)
anthropic_native infer fake-model passed None
gemini_native list passed None True ('fake-model',)
gemini_native infer fake-model passed None
```

假服务记录：`GET /v1/models`（查询参数 `limit`）、`POST /v1/messages`（`stream: false`），都只带 `x-api-key` 与 `anthropic-version: 2023-06-01`；
`GET /v1beta/models`（查询参数 `pageSize`）、`POST /v1beta/models/fake-model:generateContent`，都只带 `x-goog-api-key`。Key 都不在 URL 里。

### 2.5 清理

```bash
docker rm -f mcn-oc-anthropic mcn-oc-gemini mcn-fake-model   # exit 0
docker network rm mcn-net                                     # exit 0
docker ps -a / docker network ls 里没有 mcn- 开头的对象；t76-pg、t76-redis 仍在运行
```

## 3. 假模型服务的扩展（`tests/e2e/fake_model_server.py`）

只用标准库；OpenAI 兼容的行为与日志格式不变。新增：

- Anthropic：`POST …/v1/messages`（`stream` 为真时按官方事件顺序发 SSE，非流式返回 Message 对象）；带 `x-api-key` 或 `anthropic-version` 的 `GET …/models` 返回 Anthropic 列表格式。
- Gemini：`GET …/v1beta/models`；`POST …/v1beta/models/<id>:generateContent`；`POST …/v1beta/models/<id>:streamGenerateContent?alt=sse`。
- 回复沿用 `echo: <原文末尾>`，不支持工具调用。
- 原生协议的请求（包括列模型的 GET）也写进 `FAKE_MODEL_LOG`：原有字段之外多记 `protocol`、`kind`（`models` / `generate`）、`path`、`model`、`stream`、
  `query`（只记参数名）、`alt`，以及 `x_api_key`、`x_goog_api_key`、`anthropic_version` 请求头，验收脚本可以据此判断用的是哪个 Key。

## 4. 结论

两个协议都在真实 OpenClaw 2026.9.36 上跑通，工作台侧的同步与检测按官方文档核实并有单元与集成测试，
因此在 `SUPPORTED_PROTOCOLS` 里开放 `anthropic_native`、`gemini_native`，并增加官方预设：

| provider | 协议 | base URL | source |
|---|---|---|---|
| `anthropic` | `anthropic_native` | `https://api.anthropic.com` | https://platform.claude.com/docs/en/api/overview |
| `google` | `gemini_native` | `https://generativelanguage.googleapis.com/v1beta` | https://ai.google.dev/api |

官方预设配第三方主机返回 422 `MODEL_OFFICIAL_HOST_MISMATCH`（`test_native_official_presets`）。

## 5. 本地检查（`services/api`）

| 命令 | 结果 |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 457 files already formatted |
| `mypy app migrations tests scripts` | no issues found in 453 source files |
| `pytest -q tests/test_model_connection_checks.py tests/test_model_connections_api.py`（真实 PostgreSQL / Redis 库 15，`REQUIRE_INTEGRATION=1`） | 158 passed |
| 上面两个文件加 `tests/test_agent_model_bindings.py tests/test_model_config_script.py`（同上） | 175 passed |
| `python -m scripts.export_openapi`，然后 `apps/web` 里 `npm run generate:api && npm run typecheck`（Node 22） | 通过；OpenAPI 与 `schema.d.ts` 没有变化（协议枚举早已包含三个值） |

## 6. 未解决

- `ai.google.dev` 三个页面未能直接打开（见第 1.2 节 TODO）。
- 推理检测的输出上限 16：Anthropic 的思考常开模型、Gemini 2.5 的思考模型可能用完上限而没有文字输出；
  这时响应仍是 `type: "message"` / 有 `candidates`，按通过处理（只判断“Key + 模型 + 接口可用”，不看回复内容）。没有用真实服务商验证。
- 前端页面没有在本任务里改；`capabilities` 现在返回三个协议与三个官方预设，页面怎么展示由页面任务确认。
