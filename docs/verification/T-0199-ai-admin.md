# T-0199 AI 审批与管理区八页：核验记录

方案 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）。任务 T-0189 ~ T-0199。

## 1. 在哪里跑

| 项目 | 环境 |
|---|---|
| 后端新接口（AI 审批汇总与排序、密钥库搜索与详情、审计筛选 / 详情 / CSV 导出） | 本会话：真实 PostgreSQL / Redis（`TEST_DATABASE_URL`、`TEST_REDIS_URL`、`REQUIRE_INTEGRATION=1`） |
| 八个页面 | 本会话：vitest（jsdom）+ vite 开发服务器与 Playwright 路由模拟接口截图（1440 亮 / 暗、1280、768、390 亮 / 暗） |
| 真实全栈与浏览器 | CI 的 ci-infra：`sprint1_browser`（设置、安全、审计）、`sprint4_browser`（集成）、`sprint6_browser`（密钥库、AI 审批卡片与审批中心）、`news_browser`（新闻早报设置）、`mail_browser`、`make smoke-full`（八页的页头与全部读接口） |

## 2. 本地结果（2026-11-09）

| 检查 | 结果 |
|---|---|
| `pytest tests/test_ai_approval_summary.py tests/test_ai_tools_api.py tests/test_approval_cards.py tests/test_oc6_security.py` | A1 分支 40 passed；合并后与密钥库、契约一起 69 passed |
| `pytest tests/test_secret_catalog_queries.py tests/test_agents_api.py tests/test_oc6_security.py tests/test_credentials.py` | B6 分支 50 passed |
| `pytest tests/test_audit_queries.py tests/test_audit_export.py tests/test_security.py tests/test_contract.py tests/test_sessions_api.py` | 合并后 78 passed |
| `pytest -q tests` 全量 | 1124 passed、15 skipped（见第 4 节） |
| `ruff check .`、`ruff format --check .`、`mypy app migrations tests scripts` | 通过 |
| `python -m scripts.export_openapi` 后 `npm run check:api` | 无差异（三个后端分支合并后在主线上统一重新生成） |
| 前端 `npm run lint`、`npm run typecheck`、`npx vitest run`、`npm run build` | 通过；74 个文件 725 个测试（新增 settings-redesign 5、security-settings-redesign 4、mail-settings-redesign 10、news-settings-redesign 19、github-integration-redesign 8、secret-catalog-redesign 8、approval-center-redesign 18、audit-center-redesign 12） |
| 截图自查 | 八页共 34 个画面（含详情、编辑、异常、只读、空态），每个在 1440 亮 / 暗、1280、768、390 亮 / 暗下截图，全部没有横向滚动；合并后在主线上重拍一遍，结果相同 |

### 关键异常在哪里验证

真实浏览器验收跑主流程；需要构造服务端状态的异常（过期、并发、限流、超限等）在 vitest 里用模拟接口逐条验证，后端对应的错误码在 pytest 里用真实数据库验证。

| 异常 | 前端（vitest） | 后端（pytest） / 浏览器 |
|---|---|---|
| AI 审批过期 / 并发决定 / 响应丢失 | approval-center-redesign：倒计时按服务器时钟、到 0 禁用并重读；版本冲突刷新不报成功；丢失响应先重读同一条、不盲目重试 | test_ai_approval_summary、test_ai_tools_api；sprint6_browser 审批中心详情 |
| GitHub 缺权 / 令牌失效 / 限流 | github-integration-redesign：只缺 Actions 时保留其余三项；令牌被拒隐藏旧结果；限流显示恢复时间并保留缓存 | 既有 GitHub 集成测试；sprint4_browser |
| 修改密码 / 会话错误 | security-settings-redesign：确认不一致不发请求；当前密码错误不报成功并清空 | test_security、test_sessions_api；sprint1_browser 撤销会话 |
| 多邮箱重连 / 分配冲突 | mail-settings-redesign：需要重连的邮箱单独标出并经 Google 重连；版本冲突提示重新核对、不覆盖 | 既有邮件测试；mail_browser |
| 新闻只读 / 模块停用 / 配置写入失败 | news-settings-redesign：admin 各标签只读不写；模块停用不请求；配置文件写入失败不报完全成功 | 既有新闻测试；news_browser 只读与关闭 |
| 密钥轮换冲突 / 记录已不存在 | secret-catalog-redesign：轮换冲突不报成功并重读；记录不在时清空旧详情 | test_secret_catalog_queries；sprint6_browser 密钥库 |
| 审计跨页筛选 / 导出超限 | audit-center-redesign：筛选变了从第一页重来；导出用当前服务端筛选；“全部时间”先要求缩小范围；导出失败不生成文件 | test_audit_queries（游标绑定筛选，换条件 422）、test_audit_export（超 10000 行 422、超 31 天 422、公式防护、BOM、no-store） |

键盘：八页只用原生按钮、链接、表单控件与表格，没有自制的键盘陷阱；浏览器验收用按角色和名称的定位（`getByRole`）操作，读屏名称与可见文字一致。

## 3. 与效果图的有意差异（如实记录）

- **共通**：图里的数字、邮箱、账号、版本、日期都是示例，页面只显示接口给的真实数据；未知显示“未知 / 未核验”，不补 0。
- **AI 审批**：每张汇总卡写明统计范围（默认最近 24 小时，可选 7 天 / 全部），即将到期与高危是待审批的子集、不相加；没有“工具调用”来源（没有会话写“来源未知”）；“操作影响”只用工具的静态说明与核对链接，不另读对象；详情多“未执行”标签和“执行结果”区，把“批准”与“执行成功”分开；状态文字“已执行”改为“执行成功”（IM 卡片同步）。
- **设置**：成员列表改为表格；“添加成员”说明不发邀请邮件。
- **集成设置**：右栏平时只读显示已保存的配置，点“修改连接”才变表单；保留“GitHub 仓库列表”手填框与“连接检查结果”明细；四项权限只来自真实核验，未核验写“未核验”。
- **安全设置**：会话只显示 ID 尾号、创建 / 最近活动 / 到期与“当前浏览器”，不推测设备、系统、位置。
- **邮件设置**：不用 Google 品牌图标；署名在右侧面板改，表格只显示状态；效果图的“新开邮箱”按规范为“断开邮箱”。
- **新闻早报设置**：页头仍叫“新闻早报”（与侧栏一致）；“运行中的配置 v12”改为“最近写入配置 #config_rev”；右侧推送渠道只读显示状态，改设置只在“模型与渠道”标签；去掉“高级模型设置”链接（智能体写早报用它自己的模型）。
- **密钥库**：页面说明写“授权对象与取用记录”（没有轮换历史）；时间统一格式；取用记录标签多一列“引用名”。
- **审计日志**：只显示“当前已加载 N 条”；时间请求的 before 多留 5 分钟容忍本机时钟偏慢；没有当前名称时显示记录里的名称并标“记录时”；未知事件显示原 action。

## 4. 全量测试与 CI

本地全量：`pytest -q tests`（真实 PostgreSQL / Redis，`REQUIRE_INTEGRATION=1`）→ **1124 passed、15 skipped**，用时 47 分 35 秒。
15 个跳过是需要外部服务的用例（与此前的全量运行相同）。没有失败。

### CI（提交 93b5bd0，12 项检查全部成功）

ci-infra 运行 42650540143：compose、agents、news 三个作业的日志里都没有 `##[error]`，也没有以“失败”开头的行。与八页相关的浏览器验收输出（原文）如下。

compose 作业，`sprint1_browser.cjs`（设置、安全、审计）：

```
通过  登出后访问 /settings 被引导登录，登录后回到 /settings
通过  修改显示名与工作空间名称
通过  成员管理：创建账号并加入（viewer），对方登录后看不到成员管理、工作空间名称只读；改为 member；移出后对方立即失去访问
通过  在另一台设备登录后撤销它的会话，该设备被引导重新登录
通过  审计日志显示 12 条记录（含撤销会话、修改设置、首次安装、成员的加入 / 改角色 / 移出）
全部通过（页面无错误）
```

compose 作业，`sprint4_browser.cjs`（集成设置）：

```
通过  设置页接入 GitHub：生成 Token 的链接、读取可访问的仓库并勾选，保存后自动检查权限通过，页面不显示 Token
通过  A17：GitHub 限流时开发者页显示“限流中”与预计恢复时间，列表照常显示
全部通过（页面无错误）
```

compose 作业，`make smoke-full`：

```
通过  全部读接口返回 200：80 个（需要具体资源 ID 或 owner 角色的 50 个由各 Sprint 验收覆盖）
通过  全部页面能打开、标题正确：/、/tasks、/projects、/knowledge、/calendar、/im、/mail、/developer、/operations、/agent-servers、/agents、/runner、/ai、/settings、/settings/modules、/settings/navigation、/settings/integrations、/settings/security、/settings/mail、/agents/secrets、/settings/audit
完整冒烟全部通过（39.1 秒）
```

agents 作业，`sprint6_browser.cjs`（密钥库、AI 审批）：

```
通过  密钥库：添加密钥（密码框输入），保存后页面上只有名称与版本，没有明文
通过  审批卡片：IM 的“审批通知”会话里出现卡片，写明“由 Owner 触发”，在卡片上批准后显示“执行成功”
通过  审批中心：详情显示“执行成功”（已新建日程「浏览器验收日程 3e883716」）、触发者 Owner（私聊它）与 IM 会话链接
通过  390px 宽度下 AI 抽屉改为侧滑面板；IM、OpenClaw 智能体、智能体服务器、密钥库、AI 审批页无横向滚动
通过  安全：浏览器的 644 个请求都指向工作台自己的地址，447 个接口响应里没有密钥明文与内部地址
全部通过（页面无错误）
```

agents 作业，`mail_browser.cjs`：邮件页 6 步全部通过（“邮件页浏览器验收通过（页面无错误）”）。

news 作业，`news_browser.cjs`（新闻早报设置）：

```
通过  设置页（五个标签）：启用、推送时间 03:00、自建 NewsNow、RSS 源、排除词、ntfy；保存后写进 TrendRadar 的配置文件，密钥只显示“已设置”，状态显示 TrendRadar 在线
通过  测试推送：经 TrendRadar MCP 发到 ntfy，页面显示每个渠道的结果
通过  手机宽度（390）：早报页各标签与设置页不横向滚动
通过  viewer：能看早报、热榜等；看不到收藏、不感兴趣与设置，后端同样拒绝
新闻早报浏览器验收通过
```

CI 第一次真跑时发现并修好的问题（都在验收脚本或扫描清单里，页面代码没有改）：

- **secrets**：gitleaks 把审计测试里故意放的假凭据（`tok-SECRET-123`、`hunter2-SECRET`，用来验证导出时会去掉）当成了密钥。按惯例把这 3 处的指纹登记进 `.gitleaksignore`。
- **news，第一处**：`getByLabel(/^全局排除词/)` 同时匹配到“全局排除词”分区和文本框，改成按文本框找。“推送时间 1”加了精确匹配，否则有第二个推送时间时会同时匹配到“删除推送时间 1”按钮。
- **news，第二处**：设置页新加的局部变量 `panel` 遮住了脚本顶部的 `panel(page)` 辅助函数，改名为 `settingsPanel`。

推送前，用 vite 开发服务器 + 模拟接口按脚本原样跑过设置页那一段；`sprint1_browser` 的设置 / 成员 / 安全 / 审计几步也这样预跑过。
