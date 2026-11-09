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

CI 结果在 PR 的检查跑完后补在下面。
