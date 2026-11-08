# TrendRadar 6.11.0 核验记录（T-0151）

- 日期：2026-11-07；任务：T-0151（人类方案《Workspace 接入 TrendRadar 升级方案》，见 `docs/v2/NEWS_PLAN.md`）
- 结论：锁定 **TrendRadar 6.11.0（MCP 4.1.1）**，用官方镜像、固定 digest；配置由工作台生成并只读挂进容器；
  MCP 没有任何鉴权，只放在工作台的内部网络里，智能体只经工作台的只读工具使用其中一部分。

## 1. 锁定的版本与来源

| 项 | 值 | 来源 |
|---|---|---|
| 源码 | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) 标签 `v6.11.0`，提交 `ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce`（2026-10-13） | `git ls-remote` + 浅克隆；`version` = 6.11.0，`version_mcp` = 4.1.1 |
| 许可证 | GPL-3.0 | 仓库 `LICENSE` |
| 抓取镜像 | `wantcat/trendradar:6.11.0@sha256:3effcb5cedf5c385b50a6a32cd2599cbdbf54c9d8c52c0403406e356f88df80b`（amd64 / arm64） | Docker Hub 标签 API；本地按 digest 拉取成功 |
| MCP 镜像 | `wantcat/trendradar-mcp:4.1.1@sha256:12cdc3dcf9d76079ad57e8fc3aa0f3b322ee99fd163f5fd6b79ee43174ebdabf` | 同上 |
| 依赖（锁文件） | fastmcp 2.12.5、mcp 1.16.0、litellm 1.82.6、feedparser 6.0.12 | `pyproject.toml`、`uv.lock` |
| 配置文件版本 | config.yaml 2.4.0、timeline.yaml 1.2.0、frequency_words.txt 1.1.0、ai_interests.txt 1.1.0 | `version_configs` |

上游的 `docker/docker-compose.yml` 用浮动的 `:latest`，不照搬；工作台按上面的 digest 部署。
v6.9.1 → v6.11.0 的差别对接入没有影响（Dockerfile、entrypoint、存储表结构、timeline 都没变；`[GLOBAL_FILTER]` 新增正则）。

## 2. 运行方式（源码核验，路径相对上游仓库）

- 抓取镜像以 root 运行，没有 `USER`、`HEALTHCHECK`（`docker/Dockerfile`）。入口 `docker/entrypoint.sh`：
  配置缺 `config.yaml` 或 `frequency_words.txt` 就退出；`RUN_MODE=cron`（默认）用 supercronic 按 `CRON_SCHEDULE`
  （默认 `*/30 * * * *`，只接受数字和 `*/,-`）运行 `python -m trendradar`；`IMMEDIATE_RUN=true` 时先跑一次。
  同时启动 `python -m http.server`（`WEBSERVER_PORT`，默认 8080，监听 0.0.0.0）把整个 `/app/output`
  （含 SQLite 文件与 HTML 报告）当静态目录提供，**没有鉴权**。
- `/app/config` 只读即可（源码里没有任何写 `config/` 的地方）；写 `/app/output`、`/app/index.html`、`/tmp`。
  配置目录实际固定在 `/app/config`：AI 提示词、兴趣文件、MCP 都不看 `CONFIG_PATH`。
- MCP 镜像：`python -m mcp_server.server --transport http --host 0.0.0.0 --port ${MCP_PORT}`
  （`docker/Dockerfile.mcp`）。**本地运行实测**：FastMCP，Streamable HTTP，地址 `http://<容器>:3333/mcp`，
  `initialize` 返回 `{"name":"trendradar-news"}`、协议 `2025-06-18`、带 `mcp-session-id`；**没有鉴权、没有限流**。
  读 `/app/config/config.yaml` 与 `/app/output`。
- 数据：每天一个 SQLite 文件，`output/news/YYYY-MM-DD.db`（热榜：`news_items`、`rank_history`、`crawl_records`、
  `period_executions` 等）与 `output/rss/YYYY-MM-DD.db`（`rss_items` 等）；日期按配置的时区。
  **AI 分析（早报正文）不入库**，只出现在推送消息和 HTML 报告里（`trendradar/ai/analyzer.py`、`report/html.py`）。
- 推送在每次定时运行里、抓取与分析之后发生；是否推送由 `config/timeline.yaml` 的时间段决定
  （`schedule.enabled` + `preset: custom`，时间段 `[start, end)`，`once.push` 每天只推一次，记在当天的
  `period_executions`）。timeline 本身不触发运行，所以“07:30 推送”要求 07:30 那一刻正好有一次定时运行。
- 通用 Webhook（`trendradar/notification/senders.py:1206-1327`）：每一批 POST 一次 JSON，默认
  `{"title": "<报告类型>", "content": "<Markdown>"}`；一份报告可能分成多批（每批约 3.8 KB，带“[第 i/N 批次]”）；
  不能加签名或请求头，只能把密钥放在 URL 里；URL 用 `;` 分隔多个。

## 3. 配置格式（工作台据此生成文件）

- `config.yaml` 顶层：`app`（`timezone`）、`schedule`、`platforms`（`api_url`、`sources[{id,name,expected_domain}]`）、
  `rss`（`feeds[{id,name,url,enabled,max_age_days}]`）、`report`（`mode` daily|current|incremental）、`filter`
  （`method` keyword|ai）、`ai_filter`、`display`、`notification.channels`（feishu、dingtalk、wework、telegram、email、
  ntfy、bark、slack、generic_webhook）、`storage`、`ai`（LiteLLM 的 `provider/model`、`api_key`、`api_base`）、
  `ai_analysis`、`ai_translation`、`advanced`。缺键时用的内置默认值与示例文件不同，生成时每个键都写全。
- 内置热榜来源（NewsNow 的 id）：`toutiao` 今日头条、`baidu` 百度热搜、`wallstreetcn-hot` 华尔街见闻、`thepaper` 澎湃新闻、
  `bilibili-hot-search` bilibili 热搜、`cls-hot` 财联社热门、`ifeng` 凤凰网、`tieba` 贴吧、`weibo` 微博、`douyin` 抖音、
  `zhihu` 知乎；设了 `expected_domain` 时，链接不是该域名 https 的整个平台数据会被丢弃。
- `frequency_words.txt`：`[GLOBAL_FILTER]` 下每行一个排除词或 `/正则/`；`[WORD_GROUPS]` 下每组用空行分隔，
  `[别名]` 起头，普通词为不区分大小写的子串，`/正则/`，`词 => 显示名`，`+词` 必须同时出现，`@N` 本组最多 N 条。
  **`!词` 实际对所有组生效**（`trendradar/core/frequency.py:142,198-202`，与文件注释不符），工作台只在全局排除里用。
- AI 筛选：`filter.method: ai` + `ai_interests.txt`（自然语言兴趣），先提取标签再给每条打分，低于 `min_score` 的丢弃，
  失败退回关键词筛选。AI 提示词文件（`ai_analysis_prompt.txt`、`ai_translation_prompt.txt`、`ai_filter/*.txt`）
  从配置目录读取。
- 版本检查：每次运行都会请求 `advanced.version_check_url` 等三个 raw.githubusercontent.com 地址（`__main__.py:1684-1690`）；
  设为空字符串即关闭。工作台生成的配置一律关闭。

## 4. MCP 工具（运行实例 `tools/list` 实测 27 个，与源码一致；都没有 readOnly 标注）

| 类别 | 工具 | 工作台是否开放给智能体 |
|---|---|---|
| 只读查询 | `resolve_date_range`、`get_latest_news`、`get_trending_topics`、`get_latest_rss`、`search_rss`、`get_rss_feeds_status`、`get_news_by_date`、`analyze_topic_trend`、`analyze_data_insights`、`analyze_sentiment`、`find_related_news`、`generate_summary_report`、`aggregate_news`、`compare_periods`、`search_news`、`list_available_dates` | 选其中一部分包装成工作台只读工具（`docs/v2/NEWS_PLAN.md` 第 6 节） |
| 读配置 / 状态 | `get_current_config`、`get_system_status`、`get_storage_status`、`get_notification_channels`、`get_channel_format_guide` | 不开放（只给工作台自己查状态） |
| 有副作用 | `trigger_crawl`（抓取并写库）、`sync_from_remote`（写库）、`send_notification`（向所有已配置渠道推送任意内容）、`check_version`（经调用方给的代理访问任意地址）、`read_article` / `read_articles_batch`（把任意网址发给第三方 `https://r.jina.ai`） | **不开放**；`send_notification` 只由工作台自己在方案 B 里调用 |

返回值是 JSON 字符串（`content[0].text` 与 `structuredContent.result`）：成功带 `success: true`，失败带
`error.code`（例如没有数据时 `DATA_NOT_FOUND`，“未找到 2026-11-07 的 news 数据”）。

## 5. 对外访问

NewsNow 公共接口 `https://newsnow.busiyi.world/api/s?id=<id>&latest`（`platforms.api_url` 可改成自建实例）、
RSS 地址、大模型接口（LiteLLM）、推送渠道（各 Webhook、api.telegram.org、ntfy、Bark、SMTP）、版本检查
（工作台关闭）、MCP 的 `read_article`（不开放）。本会话的网络策略拒绝 NewsNow 与 RSS 站点（代理返回 403），
真实抓取在 CI 里跑；验收用模拟的 NewsNow、RSS 与模型服务，结果稳定、不依赖外部站点。

## 6. 对接入的约束（落到方案里）

1. MCP 与静态文件服务都没有鉴权：两个容器只加入工作台的内部网络，不发布任何端口；只有 api、worker 能访问。
2. 智能体不直连 TrendRadar MCP（不得放宽智能体的工具限制）：工作台提供只读的 `news_*` 工具，转发到上表允许的 MCP 工具。
3. 配置文件（含大模型 Key、推送凭证）由工作台按网页上的设置生成，写进共享卷，容器只读挂载；密钥在数据库里加密保存。
4. 早报正文要靠推送拿：工作台把自己的接收地址作为通用 Webhook 的第一个地址，分批接收后拼成一份早报入库。
5. 定时运行固定每 30 分钟一次，推送时间以半小时为单位，用 timeline 的自定义时间段与 `once` 实现。
6. AI 提示词文件来自上游，**不放进本仓库**（GPL-3.0）：部署时按锁定提交下载并校验 sha256，写进配置卷。

## 7. 接入后的本地实测（T-0152 / T-0153，2026-11-07）

用 `infra/trendradar/compose.yaml`（锁定的两个镜像）与工作台的配置生成器（`app/domain/news_config.py`，现为 `app/modules/news/config.py`）实际跑了一遍，
外部依赖换成 `tests/e2e/fake_news_server.py`：

| 项 | 结果 |
|---|---|
| 权限 | 配置文件属主 10001、0640；两个容器 `cap_drop: ALL` + `group_add: ["10001"]` 能读，MCP 只读根文件系统正常 |
| 提示词 | `scripts/trendradar-prompts.sh` 从 `raw.githubusercontent.com` 按提交 `ceabb027…` 下载 5 个文件，sha256 与 `infra/trendradar/prompts.sha256` 一致；再运行一次直接跳过 |
| 一次运行 | 抓 3 个平台 11 条 → 关键词筛选留下 4 条（“八卦”“绯闻”被全局排除）+ RSS 1 条 → AI 分析（假模型）→ 推到 ntfy 与通用 Webhook（工作台的接收地址在第一个），`period_executions` 记下 `push_1` |
| 通用 Webhook | 一批时 `{"title": "全天汇总", "content": "<Markdown>"}`，没有批次头；多批时每批开头 `**[第 i/N 批次]**`（`notification/batch.py` 的 wework 格式），工作台按这个拼接 |
| MCP | `search_news`、`get_latest_news`、`get_system_status` 直接读同一个输出卷；结果有缓存（改关键词后 `get_trending_topics` 不会马上变），所以“改关键词后条目随之变化”的验收看下一次运行的早报 |
| LiteLLM | 1.82.6 每次启动会去 `raw.githubusercontent.com` 取模型价格表：compose 里设 `LITELLM_LOCAL_MODEL_COST_MAP=True` 后不再访问 |
| 推送标记 | 工作台经 `send_notification` 推的消息标题以“【工作台】”开头，推回接收地址时不入库 |
| 时区 | MCP 用容器本地时间（`datetime.now()`）算“今天”，`get_latest_news`、`get_latest_rss` 没有日期参数；抓取容器按配置的 `app.timezone` 给数据分日期。容器默认 UTC 时，北京时间 0~8 点 MCP 找的是“昨天”的文件（实测 `未找到 2026-11-07 的 news 数据`，而数据在 `2026-11-08.db`）。上游 compose 给两个容器都设 `TZ=Asia/Shanghai`；本项目改为部署级设置 `.env` 的 `TRENDRADAR_TZ`（两个容器的 `TZ` 与工作台的 `NEWS_TIMEZONE` 共用），网页上只读 |
| `search_news` | 有命中时结果在 `data`、RSS 在 `rss`；热榜没有命中时直接返回 `{"results": [], "message": "未找到匹配的新闻…"}`，即使 `include_rss=true` 也不带 RSS。所以工作台的搜索与 `news_search` 工具另用 `search_rss`（结果在 `data`）查 RSS |
| `send_notification` | 每次调用都重新读配置文件：网页保存设置后马上测试推送，用的就是新的渠道（实测改 ntfy 的 topic 后推到新 topic） |
| 推送去重 | 同一个推送时间段一天只推一次（`今天已推送过，跳过`），记录在输出卷里；所以验收在全新的输出卷上跑，“改关键词后”的那次用单独的输出卷 |

CI（`ci-infra` 的 `news` job）在 Compose 全栈上重复这些步骤：`tests/e2e/news_acceptance.py` 验证入库、通知、页面接口、
智能体工具与“改关键词后下一次运行跟着变”，`tests/e2e/news_browser.cjs` 验证设置页、测试推送、早报页各标签、收藏与不感兴趣、
首页面板、手机宽度与 viewer 只读。两个脚本 2026-11-08 在本机 Compose 全栈（真实 TrendRadar 6.11.0 / MCP 4.1.1）上全部通过。
