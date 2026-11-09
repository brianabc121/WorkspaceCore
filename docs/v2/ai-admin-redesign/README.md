# Workspace AI审批与管理区八页：Claude开发交接包

范围：**AI审批、设置、集成设置、安全设置、邮件设置、新闻早报设置、密钥库、审计日志**。
本包是待实施开发文档与8张效果图，**不是已实现代码**。由人类交给Claude，当前Codex不启动业务开发。

## 阅读顺序

1. [八页产品／开发规范](docs/superpowers/specs/2026-11-09-ai-admin-design.md)：范围、权限、功能、状态和图片差异。
2. [接口与兼容约定](docs/superpowers/specs/2026-11-09-ai-admin-contract.md)：新增汇总／筛选／元数据／审计导出，及原接口复用规则。
3. [AI审批子计划](docs/superpowers/plans/2026-11-09-ai-approvals.md)：A1–A2，读侧汇总／排序与审批界面。
4. [六页管理子计划](docs/superpowers/plans/2026-11-09-admin-settings.md)：B1–B6，共享布局、设置、集成、安全、邮件、新闻、密钥。
5. [审计子计划](docs/superpowers/plans/2026-11-09-audit-center.md)：C1–C2，过滤／详情／CSV与界面。
6. assets中的8张图，按01–08逐页对照。

核对基线：`origin/main e0176b59ffcf2ae3e630d4d1b0f75addcf4b1f6c`，含首页#177、第一批四页#178、第二批四页#179。实施前重新fetch，不重复实现已合并能力。
本包未领取任务、未占用T编号、未改业务代码、未建PR、未运行产品测试或部署。MANIFEST.json记录文件大小、SHA-256和事实基线。

## 分工与顺序

| 任务 | 内容 | 依赖 |
|---|---|---|
| B1 | 设置与少量共享管理布局 | 最新主干 |
| A1 | AI汇总／筛选／排序 | 最新主干 |
| A2 | AI审批UI与决定／结果 | A1、B1 |
| B2 | GitHub集成UI／权限证据 | B1及现有#179后端 |
| B3 | 本人密码／会话UI | B1 |
| B4 | 多邮箱配置／分配UI | B1 |
| B5 | news模块设置UI | B1 |
| B6 | 密钥服务端搜索／详情与UI | B1 |
| C1 | 审计过滤／详情／CSV | 最新主干 |
| C2 | 审计UI／下载 | C1、B1 |

A1/B6/C1修改OpenAPI和生成类型，B1/A2/C2涉及路由，共享文件串行。默认按子项目可审查提交／PR；新读契约先可用再启用对应UI，不能交付依赖未部署接口的半页。若同一PR，主任务列全关联T编号。
计划里的A/B/C编号不是仓库T编号，Claude按实际未占编号和协作协议建／接手。

## 五项必须落实

- AI审批保持逐次决定、15分钟到期、critical本人密码、If-Match和固定参数；read/knowledge/direct照原授权，批准不等于执行成功。
- 个人资料／本人会话与工作空间管理区分；owner不可改，admin不越权；密码与存量Token／Secret／Webhook不回显、不缓存。
- 多邮箱分配不自动开智能体发信；Gmail继续readonly+send，不扩大修改邮件权限。
- 新闻version、config_rev、配置写入、实际运行／推送分开；dirty先保存，刷新不生成／推送，admin仍只读。
- 密钥搜索、审计过滤在服务端全量执行。审计导出同筛选、同scope，最多31天／10000行，CSV防公式注入，不含原IP／ip_hash／凭据，不新增删日志能力。

## 可直接复制给Claude

请按附件落实Workspace的AI审批、设置、集成设置、安全设置、邮件设置、新闻早报设置、密钥库、审计日志八页。
先读README、八页规范、接口约定、三个子计划和8张效果图，再读最新AGENTS.md/docs/AGENTS.md、.collab、相关身份／审批／GitHub／Gmail／news／密钥／审计契约与OpenAPI。
以最新origin/main为准，确认没有重复任务／PR或路径占用，保护未提交工作；按本次人类指派和仓库协议建／接手实际任务与分支，不恢复旧T-0149或旧生成类型。
保留已合并首页和两批四页，以及其他在途智能体／容器页面工作。本轮按B1共享布局、A1/A2审批、B2–B6管理、C1/C2审计的依赖实施，不只是换静态样式。
复用现有功能；补AI可见范围汇总与到期排序、密钥跨分页搜索／元数据详情、审计服务端筛选／安全导出。不要重复实现已有GitHub逐仓库权限证据或密钥取用secret_id筛选。
图片是视觉方向，规范中的真实权限／数据口径优先。不要编造AI理由、设备位置、审批成功、配置已运行、渠道已送达、历史密钥值或审计前后值。
保持角色、workspace、If-Match/version、CSRF、幂等、敏感输入清空、原风险授权例外和模块宿主。邮件分配不授发信、新闻admin只读、密钥库owner，不能借审计补读owner专属资源。
契约变更先后端导出OpenAPI再机械生成schema.d.ts；按子计划做必要测试并跑下列完整质量门槛，记录真实输出、错误和亮暗／手机／桌面截图。
代码与自己的任务更新同PR，标题使用[T-实际编号]简述并按.github/pull_request_template.md填写；未完成保留可恢复进度，不标假done。审查／合并按D-0004，部署、不可逆迁移、Sprint推进和改协作协议不由本包自动执行。

## 实施阶段质量门槛

以下是Claude要执行的命令，**本包整理时没有运行产品测试**。

前端apps/web，选择Node 22：

```bash
node --version
npm ci
npm run lint
npm run typecheck
npm run test
npm run check:api
npm run build
```

后端services/api，使用项目测试数据库／Redis：

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests scripts
uv run python -m scripts.export_openapi
uv run python -m scripts.export_openapi --check
uv run pytest
```

生成顺序：export→前端npm run generate:api→审阅并同步生成文件到提交／索引→npm run check:api（内部用git diff验证零漂移）。不手改输出。若新增索引迁移，在临时库升级／降级／再升级并记录，不能拿生产库验证。

仓库根：

```bash
python3 scripts/collab.py check
git diff --check
```

浏览器验收：8页各桌面／手机、亮暗及键盘；另覆盖AI过期／并发决定、GitHub缺权／限流、密码／会话错误、多邮箱重连／分配冲突、新闻渲染失败／admin只读／模块停用、密钥轮换冲突、审计跨页筛选／导出注入／超限。
复用项目现有fake服务与浏览器启动方式，不向真实邮箱／IM发测试消息，不连接真实用户账号来截图；无法验证如实记录阻塞，不能把列出的命令当成已通过结果。

## 效果图

- [01 AI审批](assets/01-ai-approvals.png)
- [02 设置](assets/02-settings.png)
- [03 集成设置](assets/03-integrations.png)
- [04 安全设置](assets/04-security.png)
- [05 邮件设置](assets/05-mail-settings.png)
- [06 新闻早报设置](assets/06-news-settings.png)
- [07 密钥库](assets/07-secrets.png)
- [08 审计日志](assets/08-audit.png)
