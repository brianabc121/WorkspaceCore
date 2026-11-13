# 新闻早报方案：接入 TrendRadar（T-0151 ~ T-0155）

> 依据：人类 2026-11-07 的设计文档《Workspace 接入 TrendRadar 升级方案》（Claude Docs）与指令「按照这份设计方案进行全面落实」；
> 核验记录 `docs/decisions/2026-11-07-trendradar-verification.md`。本文把那份方案落到工作台现有的架构上，
> 一期、二期一起实施；与原文不同的地方都在第 2 节写明原因。

## 1. 目标（来自人类方案）

1. 每天早上（默认 07:30）手机收到一份早报：各平台热榜 + 订阅的 RSS 中与兴趣相关的条目，附 AI 摘要。
2. 工作台首页有“今日早报”面板，能看完整内容和历史。
3. 工作台里的智能体能回答“今天 AI 有什么新闻”“最近一周关于某公司的报道”。
4. 不需要任何新闻平台账号，只要一个大模型 Key 和一个推送渠道。
5. 二期：在工作台里设置兴趣（主题、关键词、排除词、RSS 源）并写回 TrendRadar；收藏 / 不感兴趣；按反馈调整兴趣；
   方案 B（工作台定时让智能体写早报）；自建 NewsNow / RSSHub 作为备用数据源。

## 2. 与原方案的差异（为遵守仓库规则）

| 原方案 | 落实方式 | 原因 |
|---|---|---|
| agent 的 MCP 配置里直接加 TrendRadar（stdio） | 工作台提供只读工具 `news_*`，由工作台转发给 TrendRadar MCP 的一部分只读工具 | 规则：不得放宽 OpenClaw 智能体的工具限制，智能体只用工作台工具；TrendRadar MCP 有推送、抓取、读任意网址等工具且没有鉴权（核验第 4 节） |
| 手改 `.env`、`config.yaml`、`frequency_words.txt` | 全部在网页上设置：工作台保存设置（密钥加密），生成 TrendRadar 的配置文件写进共享卷，容器只读挂载 | 人类 2026-11-06：用户要设置的东西尽量放在网页上；密钥不进仓库、不进浏览器 |
| `docker compose` 用 `:latest`，端口映射到本机 | 锁定 6.11.0 / 4.1.1 的 digest；两个容器只在工作台的内部网络里，不发布端口 | 规则：生产禁止浮动 latest；MCP 与静态文件服务没有鉴权 |
| 方案 A / B 二选一，一期用 A | 两种都做，设置里选：A = TrendRadar 自带 AI 简报与推送；B = 工作台到点让一个智能体写早报 | 人类要求全面落实 |
| “自建 NewsNow 或 RSSHub 作为备用数据源” | 设置里可以填自建 NewsNow 的地址；RSSHub 的订阅就是普通 RSS 源。本方案不代为部署 NewsNow / RSSHub | 两者是独立的第三方服务，按需由管理员自建；工作台只负责指向它们 |
| “读取 TrendRadar 的 output/ 数据或 agent 存下的简报” | 早报正文经通用 Webhook 推给工作台入库；热榜、RSS、搜索经 MCP 查询 | 核验：AI 简报不入库，只在推送与 HTML 里；MCP 是上游提供的查询接口，比直接读它的 SQLite 稳定 |

## 3. 架构

```
NewsNow / RSS / 大模型 / 推送渠道（外网）
          ▲                    ▲
          │ egress             │ egress（send_notification 用）
  trendradar（抓取 + 筛选 + AI 简报 + 推送，每 30 分钟一次）     trendradar-mcp（只读查询，HTTP :3333/mcp）
          │  ▲ 只读挂载 /app/config                                  │ 只读挂载 /app/config、/app/output
          │  └──────────── 共享卷 trendradar-config ◄────── api / worker 生成配置文件
          │ 通用 Webhook（news 内部网络）                             ▲
          ▼                                                          │ news 内部网络
   api：POST /internal/news/ingest/<token> → 早报入库、通知          api / worker：MCP 客户端（只调允许的工具）
                                                                     ▲
                                              智能体 news_* 只读工具、早报页面、首页面板
```

- **网络**：core 新建内部网络 `news`（`internal: true`，不通外网），api、worker 加入；TrendRadar 两个容器加入 `news`
  和它们自己项目的出网网络。只有 api、worker、两个 TrendRadar 容器在 `news` 上。
- **卷**：core 新建 `trendradar-config`，api、worker 以读写挂载在 `/var/lib/workspace/trendradar`；TrendRadar 两个容器只读挂载为
  `/app/config`。`trendradar-output`（抓到的数据）只属于 TrendRadar 项目，MCP 只读挂载。
- **一个部署只有一个 TrendRadar**：同一时刻只有一个工作空间能启用早报（数据库唯一约束），其余工作空间看到“已被另一个工作空间使用”。
- **部署好就默认打开**（T-0225，人类 2026-11-13 要求）：一键部署脚本部署好 TrendRadar 后，以首次安装创建的管理员身份按默认设置打开他的工作空间的早报（`connect news-enable`）；只在还没保存过设置、也没有别的工作空间打开时打开，保存过的设置（包括关掉的）不改。

## 4. 设置（网页 → TrendRadar 配置文件）

设置页 `设置 → 新闻早报`，只有 owner 能改，保存即重新生成配置文件（每次运行 TrendRadar 都重新读取，不用重启）。

| 设置 | 生成到 | 说明 |
|---|---|---|
| 启用 | `notification.enabled`、生成 / 清空配置 | 关闭时生成“不抓取、不推送”的配置 |
| 推送时间（1~3 个，半小时为单位，默认 07:30） | `timeline.yaml` 的 `custom` 时间段 | 时间段 `[t, t+30min)`，`once.push`、`once.analyze`，`report_mode: daily`；其余时间只抓取 |
| 时区（默认 Asia/Shanghai；部署时在 `.env` 的 `TRENDRADAR_TZ` 设置，网页上只读） | `app.timezone` 与两个容器的 `TZ` | 决定推送时间与“今天”的日期；MCP 按容器时区算“今天”，所以不能在网页上改（核验记录第 7 节） |
| 早报方式 A / B，方案 B 选一个智能体实例 | A：时间段推送；B：时间段不推送，由工作台调智能体 | 第 5 节 |
| 兴趣主题（名称、关键词、必含词、每组最多条数、给 AI 的描述） | `frequency_words.txt` 的 `[WORD_GROUPS]`；`ai_interests.txt` | 关键词支持 `/正则/`；偏好学习会调整条数（第 8 节） |
| 全局排除词 | `frequency_words.txt` 的 `[GLOBAL_FILTER]` | 上游的 `!词` 实际是全局的，所以只做全局排除 |
| 热榜平台（核验过的 11 个）+ 自建 NewsNow 地址 | `platforms.sources`、`platforms.api_url` | 带 `expected_domain` |
| RSS 源（名称、地址、启用、只看最近几天） | `rss.feeds` | RSSHub 的订阅也填在这里 |
| 筛选方式：关键词 / AI | `filter.method` | AI 筛选用 `ai_interests.txt`，失败时上游退回关键词 |
| 大模型：模型（LiteLLM 格式，如 `deepseek/deepseek-chat`、`openai/gpt-4o-mini`、`ollama/qwen2.5`）、接口地址、Key、是否翻译 | `ai.*`、`ai_analysis.enabled`、`ai_translation.enabled` | Key 加密保存；Ollama 这类不要 Key 的，工作台填一个占位值（上游要求有 Key） |
| 推送渠道：飞书、钉钉、企业微信、Telegram、邮件、ntfy、Bark、Slack、通用 Webhook | `notification.channels.*` | 凭证加密保存；工作台的接收地址总是第一个通用 Webhook |

固定写入：版本检查地址为空（不访问 GitHub）、`storage.backend: local`、`retention_days: 30`、`report.mode: daily`。
AI 提示词文件来自上游（GPL-3.0，不放进本仓库）：部署时按锁定提交下载、校验 sha256 后写进配置卷（`make trendradar-prompts`）。

## 5. 早报

- **方案 A（默认）**：到推送时间那次运行，TrendRadar 做全天汇总 + AI 分析，推到所选渠道和工作台。工作台的接收接口
  `POST /internal/news/ingest/<token>`（Caddy 只转发 `/api/*`，这个地址只在 `news` 网络里可达；token 32 字节随机，加密保存）
  按“[第 i/N 批次]”把多批拼成一份，存入 `news_briefings`，给工作空间的 owner 发一条站内通知（“今日早报已送达”）。
  标题以“【工作台】”开头的是工作台自己经 TrendRadar 推的（测试推送、方案 B），接收时忽略。
- **方案 B**：到推送时间，worker 在“每日早报”项目里建一个任务（执行者 = 选定的智能体，说明里写兴趣、格式和要用的工具），
  再给这个实例安排一个立即执行的**唤醒**（`agent_wakeups.source = news`，T-0121 的机制：接入包每分钟领取，在独立会话里跑一轮；
  不需要管理员的 IM 账号）。智能体用 `news_*` 工具取新闻，用 `readme_update` 把早报写进任务 README 的「早报」部分，
  再 `complete_task`（都是已有的、不用审批的工具：例外二、例外五）。任务完成时工作台取出这一部分存为早报，
  并经 TrendRadar MCP 的 `send_notification` 推到所选渠道（这是工作台自己调用，不开放给智能体）。90 分钟没完成记为失败并通知 owner。
- 两种方式的早报都在“早报”页面和首页面板里看，保留全部历史。

## 6. 智能体工具（只读，工作台转发到 TrendRadar MCP）

| 工具 | 转发到 | 说明 |
|---|---|---|
| `news_latest` | `get_latest_news` | 最新一批热榜，可按平台，最多 50 条，带链接 |
| `news_search` | `search_news` | 按关键词搜热榜（可含 RSS），日期范围可选，最多 50 条 |
| `news_by_date` | `get_news_by_date` | 某天或某段日期的热榜 |
| `news_trending` | `get_trending_topics` | 热点话题统计 |
| `news_rss` | `get_latest_rss` | 最近几天的 RSS 条目 |
| `news_related` | `find_related_news` | 与一条标题相关的新闻 |
| `news_briefing` | 工作台数据库 | 今天或某天的早报 |

只读、不审批；只有启用了早报的工作空间的实例看得到。参数在工作台校验（条数上限、日期格式），超时 15 秒，
MCP 返回的错误按 `error.code` 归类（如没有数据）。工作台网页的“热榜 / RSS / 搜索”也走同一个客户端。

## 7. 页面

- **早报**（导航新增，`/news`）：今天（最新一份早报 + 每个兴趣主题今天的条目）、往期、热榜（按平台）、RSS、搜索、收藏。
  条目可以“收藏”或“不感兴趣”（可顺手把标题里的某个词加进全局排除）。成员与访客都能看；设置只有 owner 能改。
- **首页面板**：新组件 `news_briefing`“今日早报”（配置：显示开头几行），显示最新一份的时间、标题和开头几行，点开进早报页。
- **设置 → 新闻早报**：第 4 节的全部设置、状态（TrendRadar 是否在线、最近一次抓取、最近一份早报、配置写入时间与错误）、
  “发一条测试推送”、方案 B 的“现在生成一份”、学到的偏好（第 8 节）。

## 8. 收藏、不感兴趣与偏好学习

- 反馈存在 `news_feedback`（谁、哪条、收藏 / 不感兴趣、来源平台或 RSS、所属主题）。
- 每次生成配置时按最近 60 天的反馈计算：
  - 每个主题的分数 = 收藏数 − 不感兴趣数；分数高的主题排在前面、每组条数上调（最多 +10），分数低的下调（最少 3 条）；
  - AI 筛选模式下，`ai_interests.txt` 写入“更关注：<高分主题>”“少推：<低分主题>”；
  - 某个来源被“不感兴趣”很多次（≥ 5 次且多于收藏）时，在设置页提示可以关掉它（不自动关）。
- 设置页列出这些调整，管理员能看到为什么变了；worker 每天 03:10 重新生成一次配置。

## 9. 存储与迁移（`0048`）

| 表 | 内容 |
|---|---|
| `news_settings` | 每个工作空间一行：启用（全部署唯一）、推送时间、早报方式、智能体实例、平台、NewsNow 地址、RSS 源、兴趣主题、全局排除词、筛选方式、大模型（模型、地址、Key 引用）、推送渠道（非密字段 + 加密的凭证引用）、接收 token 引用、配置版本与写入时间 / 错误、乐观锁 `version` |
| `news_briefings` | 早报：日期、来源（trendradar / agent）、标题、正文（Markdown，≤ 200 KB）、已收批数 / 总批数、状态（receiving / complete / failed）、方案 B 的任务 |
| `news_feedback` | 收藏 / 不感兴趣（唯一：工作空间 + 用户 + 类型 + 链接） |
| `agent_wakeups.source` | 增加 `news`（方案 B 的唤醒） |

密钥（大模型 Key、推送凭证、接收 token）用现有的凭证加密，纳入密钥轮换（`credential_rotation`）。

## 10. 部署

- `infra/trendradar/compose.yaml`（项目 `<core>-trendradar`），`make trendradar-pull`、`trendradar-prompts`、`trendradar-up`、
  `trendradar-down`、`trendradar-logs`；`deploy.sh` 的可选组件里加“新闻早报（TrendRadar）”，安装 / 升级时一起拉镜像、下载提示词、启动。
- 定时运行固定每 30 分钟（`TRENDRADAR_CRON`，验收时改成每分钟）。
- TrendRadar 抓到的数据（`trendradar-output`）保留 30 天，不进备份（都是公开数据）；早报、设置、反馈在数据库里，随工作台备份。
- 运维页“服务”里加 TrendRadar（MCP 能否连通、最近一次抓取时间）。

## 11. 安全

- MCP 与静态文件服务没有鉴权：只在 `news` 内部网络里，不发布端口；工作台的 MCP 客户端只调第 6 节和状态类工具（白名单），
  `send_notification` 只在方案 B 里由工作台调用。
- 智能体只读、不审批（读工具）；方案 B 只用已有例外里的工具，不新增例外。
- 用户填的地址（RSS、NewsNow、推送 Webhook、大模型接口）：只允许 http(s)，拒绝回环、链路本地、私有地址和没有点的主机名
  （验收用的模拟服务经 `NEWS_ALLOWED_HOSTS` 放行）。这些地址由 TrendRadar 容器访问，不由 api 访问。
- 接收接口：token 常量时间比较、每批 ≤ 64 KB、按工作空间限流，只在启用时接受；正文按 Markdown 安全渲染（不执行 HTML）。
- 密钥只写进配置卷（文件权限 0640），不进仓库、不回传浏览器（只显示“已设置”）。
- GPL-3.0：工作台只通过接口调用、生成配置，不复制、不修改它的代码与提示词。

## 12. 验收（对应人类方案的验收标准）

CI 里用真实的 TrendRadar 6.11.0 两个容器，配上模拟 NewsNow / RSS / 大模型 / 推送接收（不依赖外部站点）：

- [ ] 网页上启用并填好设置后，到推送时间收到早报：模拟推送渠道收到消息，工作台入库一份完整早报并有站内通知
- [ ] 早报里是与兴趣相关的条目，每条带原文链接；排除词生效
- [ ] 智能体工具 `news_search` / `news_latest` / `news_briefing` 返回真实数据；不开放的 MCP 工具调不到
- [ ] 修改关键词后，配置文件随之变化，下一次运行的条目随之变化
- [ ] 方案 B：到点建任务、智能体写 README 并完成后入库与推送（接口级测试）
- [ ] 收藏 / 不感兴趣影响下一次生成的配置（条数与排序、AI 兴趣文件）
- [ ] 密钥不在仓库、不在浏览器存储与接口响应里；MCP 不能从工作台网络以外访问
- [ ] 浏览器验收：设置页、早报页、首页面板、收藏 / 不感兴趣、手机宽度

“连续 7 天每天 07:30 前收到”需要真实部署运行，由人类在部署后观察。

## 13. 任务

| 任务 | 内容 |
|---|---|
| T-0151 | 核验记录（本 PR） |
| T-0152 | 部署：compose、`news` 网络与配置卷、提示词下载、Make、deploy.sh、运维页服务、文档 |
| T-0153 | 后端：迁移 0048、设置与密钥、配置生成、接收早报、MCP 客户端与 `news_*` 工具、页面接口、首页模块、反馈与偏好学习、方案 B |
| T-0154 | 前端：早报页、首页面板、设置页、收藏 / 不感兴趣 |
| T-0155 | 验收：CI 里真实 TrendRadar + 模拟数据源；浏览器验收；核验记录补实测 |
