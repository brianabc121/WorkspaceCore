# T-0208 中转站管理与 OpenClaw 主模型：核验记录

方案 `docs/v2/model-connections/`（README、设计、接口约定、三份实施计划、验收矩阵、迁移手册、效果图）。任务 T-0200 ~ T-0208。
决定与 OpenClaw 核验：`docs/decisions/2026-11-09-model-connections.md`。

报告里分清三种证据：**单元 / 模拟**（pytest、vitest 里的模拟服务商或模拟接口）、**真实 OpenClaw + 假上游**（锁定镜像 + 假模型服务）、
**真实服务商**（没有做：没有用户的真实 Key，不声称在真实 OpenAI / Anthropic / Google 上验证过）。

## 1. 环境

| 项目 | 值 |
|---|---|
| 基线 | main `bc74494` |
| OpenClaw | `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`（本机已缓存） |
| Python / Node | 3.12（services/api 的 venv）/ 22.23.4 |
| 数据库 | 本会话自己的 PostgreSQL 16 与 Redis 7 容器（`TEST_DATABASE_URL`、`TEST_REDIS_URL`、`REQUIRE_INTEGRATION=1`；不写密码） |

## 2. OpenClaw 核验（MC-0，真实 OpenClaw）

在 `--network none` 的容器里运行锁定镜像的 CLI 与网关，用容器内的抓包服务记录 OpenClaw 发出的每个请求（假 Key）。
结论写在 ADR 第 2 节；原始输出在本会话的临时目录（核验时的 `oc-verify/out/`：`key-excerpts.md`、`validate-*.txt`、`capture/summary.txt`、
`live/gateway-reload-log.txt`、`config-schema.json`），没有提交进仓库（含镜像代码摘录）。要点：

- `api` 取值 12 个；`openai-completions` → `POST <baseUrl>/chat/completions` + `Authorization: Bearer`；
  `anthropic-messages` → `/v1/messages` + `x-api-key` + `anthropic-version: 2023-06-01`；`google-generative-ai` → `/models/<id>:streamGenerateContent?alt=sse` + `x-goog-api-key`；
- `models.providers.*` 与 `agents.defaults.model` 热加载（约 0.2 秒），无效配置跳过、保留上一份；同一 SecretRef 换值要 `secrets reload`；
- `config patch --stdin [--dry-run --allow-exec --json]` 一次校验写入；失败时 `checks` 仍为 true，类别在 `errors[].kind`（本次实测补充）；
- provider 键要小写、不能含 `/`；`models[]` 每项要 `id` 与 `name`；入口的 `doctor` 会改写配置文件，只能打补丁不能整份覆盖。

## 3. 本地结果

（待补：后端全量、前端全量、真实 OpenClaw 验证脚本、接入包配置校验、原生协议核验。）

## 4. CI 结果

（待补。）

## 5. 验收矩阵

（待补：A01 ~ A11、M01 ~ M13、R01 ~ R10、S01 ~ S04、U01 ~ U05 逐项写测试名与证据类型。）
