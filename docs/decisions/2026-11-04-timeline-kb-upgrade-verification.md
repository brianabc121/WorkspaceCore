# 2026-11-04 时间轴看板与知识库升级的核验：OpenClaw 用量、Cloudflare R2、阿里云 OSS（T-0101）

## 1. 背景

`docs/v2/TIMELINE_PLAN.md` 依赖三个外部接口：
- OpenClaw 的 token 用量：时间轴看板显示每段执行用了多少 token；
- Cloudflare R2 的 S3 兼容接口：知识库图片的存储；
- 阿里云 OSS 的 S3 兼容接口：同上。

依据 `docs/AGENTS.md` 第 8 节第 3 条与 V2 补充规则：只认锁定版本的源码、随版本发布的文档或运行实例，不按概念名称猜；核验不了的写成接口桩和 TODO。

## 2. OpenClaw 2026.9.36 的用量（已在运行实例上核验）

镜像 `openclaw/openclaw:2026.9.36@sha256:eefba69ec4a5fa596f3b1a8fac4f09525ce10435617a23f2a2090ddd3b6c1c2f`。

### 2.1 随版本文档

镜像里的 `/app/docs`：
- `concepts/usage-tracking.md`：
  - token 与成本来自 OpenClaw 自己的会话记录；
  - 网关方法 `usage.cost`、`sessions.usage` 按日期范围返回汇总，默认 UTC；
  - `mode: "specific"` 可以带 IANA `timeZone`。
- `gateway/protocol/rpc-system-and-channels.md`：
  - `sessions.usage` 返回每个会话的用量汇总；`agentScope: "all"` 汇总所有 agent；
  - `sessions.usage.timeseries` / `sessions.usage.logs` 返回单个会话的明细。
- `gateway/opentelemetry/spans-and-events.md`：
  - 外部集成要按会话取用量时，用 `openclaw gateway call sessions.usage --params '{"range":"30d","agentScope":"all"}' --json`，要 `operator.read`；
  - OpenTelemetry 的用量指标故意不带会话标识；
  - 缓存刷新期间某个会话的用量可能暂时为 `null`。
- 不用 OpenTelemetry 的原因：要装 diagnostics-otel 插件，而实例检查清单只允许 `@openim/openclaw-channel` 一个插件。

### 2.2 实测（2026-11-04，本机）

- **环境**：
  - 网关：`node openclaw.mjs gateway`，令牌认证，与接入包相同；
  - 模型：指向 `tests/e2e/fake_model_server.py`，每次调用回报 `prompt_tokens=1, completion_tokens=1`。
- **步骤**：
  1. `node openclaw.mjs agent --session-id usage-probe-1 --message ... --json` 调两轮；
  2. 再调 `node openclaw.mjs gateway call sessions.usage --params ... --json`。
- **结果**：
  - 第一次调用缓存是冷的：`cacheStatus.status = "refreshing"`，会话的 `usage` 为 `null`，`computing: true`；几秒后再调为 `"fresh"`。
  - 每个会话的 `usage` 字段（摘录）：

    ```json
    {"input": 2, "output": 2, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 4,
     "sessionId": "usage-probe-1", "firstActivity": 1793757159332, "lastActivity": 1793757165856,
     "messageCounts": {"total": 4, "user": 2, "assistant": 2, "toolCalls": 0, "toolResults": 0, "errors": 0}}
    ```

    两轮各 1 个输入、1 个输出 token，与假模型回报的一致。会话的 `key` 形如 `agent:main:explicit:usage-probe-1`；定时任务的会话是 `agent:main:cron:<id>`。
  - 顶层还有 `totals`（同样的字段，跨会话合计）和 `aggregates.daily`（`{date, tokens, ...}`）。
  - 参数：
    - `range` 只接受常量：`7d`、`30d`、`90d`、`all` 可用；`today`、`1d` 返回 `INVALID_REQUEST`；
    - `{"mode":"specific","startDate":"2026-11-04","endDate":"2026-11-04","agentScope":"all"}` 可用，返回当天的 4 个 token。

### 2.3 结论

- 上报脚本用 `mode: "specific"`，起止日期都是今天（UTC），取每个会话的 `input` / `output` / `cacheRead` / `cacheWrite`。按（实例、日期、会话）算增量：
  - `usage` 为 `null` 的会话跳过；
  - 值变小就以新值为准，不出负数。
- 不用 `range: "7d"` 一类的滑动范围：会话里旧日子的用量会滑出范围，累计值变小，增量会算错。
- 刚过零点的 10 分钟内，昨天也再报一次，免得丢掉零点前最后一分钟的用量。
- 网关令牌有 `operator.read`；脚本在容器里运行，令牌不离开容器。

## 3. Cloudflare R2 与阿里云 OSS（部分核验）

本环境的网络策略拦截了 `developers.cloudflare.com` 和 `help.aliyun.com`（DNS 解析失败），官方页面打不开；只能读到这些官方页面的搜索结果摘要（2026-11-04）。

### 3.1 Cloudflare R2

来源（搜索摘要）：
- https://developers.cloudflare.com/r2/reference/data-location/
- https://developers.cloudflare.com/r2/objects/upload-objects/
- https://developers.cloudflare.com/r2/objects/download-objects/
- https://developers.cloudflare.com/r2/platform/release-notes/

- endpoint：`https://<ACCOUNT_ID>.r2.cloudflarestorage.com`；有辖区的桶用 `https://<ACCOUNT_ID>.<JURISDICTION>.r2.cloudflarestorage.com`，辖区 endpoint 访问不到辖区外的资源。
- 签名的 region 用 `auto`。SDK 要求填，R2 不使用；空字符串和 `us-east-1` 也视同 `auto`。
- 路径风格（endpoint/bucket/key）可用；虚拟托管风格从 2022 年起支持。
- **未确认**：PutObject / GetObject / HeadObject / DeleteObject 的完整支持说明，在 S3 API 兼容性页面上，没读到。

### 3.2 阿里云 OSS

来源（搜索摘要）：
- https://help.aliyun.com/zh/oss/developer-reference/compatibility-with-amazon-s3
- https://help.aliyun.com/zh/oss/developer-reference/use-aws-sdks-to-access-oss
- https://help.aliyun.com/zh/oss/user-guide/0002-00000714

- 兼容 S3 API；用 AWS SDK 访问时改 endpoint 与凭证即可。
- endpoint：`https://s3.oss-<region>.aliyuncs.com`，例如 `https://s3.oss-cn-hangzhou.aliyuncs.com`。
- **只支持虚拟托管风格**：bucket 必须在子域名里，`https://<bucket>.s3.oss-<region>.aliyuncs.com/<key>`；路径风格会被拒绝。
- 支持 AWS Signature V4；V2 签名的 POST 请求不可用，所以统一用 V4。
- 不支持 `aws-chunked` 编码：整块上传，`x-amz-content-sha256` 填正文的 SHA-256。
- `SignatureDoesNotMatch` 的常见原因：region 与 bucket 所在地域不一致，或代理加了额外的请求头。

### 3.3 结论与 TODO

- 实现一个通用的 S3 兼容客户端：自己实现 SigV4，不引入 SDK。
  - endpoint、region、寻址方式（路径 / 虚拟托管）都可配置；
  - R2 默认 region `auto`、路径风格；OSS 默认 `s3.oss-<region>.aliyuncs.com`、虚拟托管风格；
  - 只用 PUT / GET / HEAD / DELETE 四种对象操作，整块上传，正文哈希写进 `x-amz-content-sha256`。
- 部署时自检：写一个探测对象、读回、删除，失败就在运维页和启动日志里给出明确原因。它在真实配置上验证 R2 / OSS 的行为，弥补文档没读全。
- CI：
  - 在已锁定的 Garage `dxflrs/garage:v2.4.1`（S3 兼容，OpenIM 已在用）上验证路径风格的读写删；
  - SigV4 用 AWS 官方文档的测试向量做单元测试，包括虚拟托管风格的规范请求。
- **TODO（T-0103 / T-0106）**：真实 R2 与 OSS 账号上的验收。要等人类提供测试桶，或在环境设置里放开 `developers.cloudflare.com`、`help.aliyun.com` 后补读完整文档。在那之前，这一条验收标准不勾选。
