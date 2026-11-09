# T-0188 容器服务器改版：核验记录

方案 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图），
决定与 Docker 字段核验 `docs/decisions/2026-11-09-runner-container-metrics.md`。任务 T-0184 ~ T-0188。

## 1. 在哪里跑

| 项目 | 环境 |
|---|---|
| CPU 采样器、上报协议、读数口径 | 本会话：单元测试（不需要 Docker）+ 本会话自己的 Docker 29.9.2 上用 `app/runner_agent` 的 `Agent.report()` 实测 |
| 读数存储、新鲜度、详情、分页搜索、迁移 0055 | 本会话：真实 PostgreSQL / Redis（`TEST_DATABASE_URL`、`TEST_REDIS_URL`） |
| 页面 | 本会话：vitest（jsdom）+ vite 开发服务器与 Playwright 路由模拟接口截图（亮 / 暗，1440 / 1280 / 390） |
| 真实容器 + gVisor + runner-agent + 浏览器 | CI 的 ci-infra agents 任务：多实例端到端的场景 26（真实 OpenClaw 调 `env_*` 工具）与 `runner_browser.cjs`（真实 sshd 部署 runner-agent） |

## 2. 本地结果（2026-11-09）

| 检查 | 结果 |
|---|---|
| `pytest tests/test_runner_cpu_metrics.py tests/test_runner_agent.py` | 37 passed（契约数值 0.6 核 / 2 秒、3 核；首样本、重启、同名重建、回退、缺失、窗口 <1 秒与 >180 秒、prune；报告兼容与拒收 NaN / 负数 / 单边字段；单个容器 stats 失败不丢条目；卷 Size=-1 为未知） |
| 真实 Docker 上的 `Agent.report()` | 两个带工作台标签的容器：第一次都是未知；7.4 秒后忙循环 0.985 核、`sleep` 的 0.0；报告通过服务端 `RunnerReport` 校验 |
| `pytest tests/test_runner_metrics.py` | 20 passed（百分比、>100%、真实 0、180 秒边界、主机离线 / 停用 / 太久没联系、旧值无时间、未运行、归档后数据卷、写入与清空规则） |
| `pytest tests/test_runner_metrics_api.py`（真实库） | 9 passed（服务端时间、poll 不刷新、旧 agent 兼容、过期、退出后未运行；主机离线即过期；不能写别的工作空间；member / viewer 403、跨空间与不存在 404；原智能体删除后仍可见；501 条分页 11 页不重不漏、最后一条可搜、`%` 按字面、游标换条件 / 换空间 422；按实时 CPU / 内存排序；GET 不发指令） |
| `pytest tests/test_runner_metrics_migration.py`（临时库） | 1 passed（0054 → 0055 新列为空、约束生效、downgrade 保留 mem/disk、再 upgrade） |
| runner 回归（`test_runner_api.py`、`test_runner_reclaim.py`、`test_runner_ssh.py` 等，连同上面） | 81 passed |
| `ruff check .`、`ruff format --check .`、`mypy app migrations tests scripts` | 通过（431 个源文件） |
| 前端 `npm run lint`、`npm run typecheck`、`npx vitest run`、`npm run build`、`npm run check:api` | 通过；66 个文件 641 个测试（新增 container-metrics 8、container-server-pages 9、container-server-detail 7） |
| 截图自查 | 1440 / 1280 / 390、亮 / 暗、四个画面（总览、详情、配置与回收、智能体用量）都没有横向滚动；详情一栏只在 1440 以上展开，更窄放进抽屉 |

## 3. CI 验收覆盖

场景 26（`tests/e2e/agents_acceptance.py`，真实 Docker + gVisor + runner-agent，接入包原样安装，所以跑的是新的 runner-agent）新增：

- gVisor 会话环境：内存有真实读数与上报时间；CPU 要么是有效的窗口平均，要么如实为“未知”（读不到 runsc 的累计时间时不能是假数字）；
- runc 常驻环境：两次报告后空闲 CPU < 0.3 核；容器里跑满一个核后 0.5 ~ 1.5 核，窗口 1 ~ 180 秒，百分比 = 核数 ÷ 配置的 cpus；内存、数据卷有读数；
- 列表按实时 CPU 排序时这个容器排第一，响应带 `total`；
- `env_stop` 之后 CPU / 内存为“未运行”（不是 0），数据卷读数还在。

`runner_browser.cjs`：页面标题“容器服务器”（地址仍是 `/runner`）、没有服务器时引导到“服务器设置”、SSH 部署到上线、
主机条显示在线 / gVisor 可用 / 最近联系、四个统计卡、旧链接 `?tab=environments` 换到容器总览并保留筛选、
详情直链遇到不存在的容器显示“不存在或已无权访问”、智能体用量 / 环境模板 / 执行记录 / 服务器设置都能打开、390px 无横向滚动、卸载、吊销、移除。

## 4. 与方案的差异（如实记录）

- **数据卷读不到时保留上次读数**（约定第 4 节写的是“该项未知”）：`disk_bytes` 是回收规则的输入，清空会让“常驻数据超过快照上限只停止不归档”的保护在那一轮失效；
  页面按原时间显示为“已过期”。理由见 ADR。
- **CI 场景用一个智能体的两个容器**比较忙 / 闲（方案写“两个智能体各自的环境”）：场景 26 只给甲打开了运行环境权限（OpenClaw 启动时就取好工具列表）；
  两个智能体的归属、筛选与“原智能体已删除”由接口测试覆盖（`test_runner_metrics_api.py`、`container-server-pages.test.ts`）。
- **“主机在线但报告中断”与“旧 agent 不带 CPU”**在接口测试里模拟（主机 `last_seen_at` 超过 90 秒、报告不带 CPU 字段），没有在 CI 里停掉真实 runner-agent 的报告线程。
- **容器重启后 CPU 先未知再恢复**由单元测试（同一 ID、新的启动代次）与本会话 Docker 的 restart 实测（计数器归零、StartedAt 变化）覆盖；CI 里时间窗口不确定，没有断言“重启后第一次一定是未知”。
- 效果图里“前端构建 0.42 / 2 核”等数字只是示例；页面不写入任何默认业务数据。

## 5. CI 结果

提交 2fde6cd 上 12 项检查全部成功。ci-infra 的 agents 作业：运行 42627209264，作业 129193943620（13:38–14:23 UTC），日志里没有一条“失败”。

场景 26 新增检查的实际读数（日志原文的数值）：

| 检查 | 读数 |
|---|---|
| gVisor 会话环境（runsc） | CPU 是有效值：`cpu_status` ok，0.000569 核，窗口 71.1 秒；内存 24,096,768 字节；数据卷 12 字节 |
| runc 常驻环境，空闲 → 跑满一个核 | 空闲 2.17e-5 核；跑满 0.854 核，窗口 71.7 秒；配置 2 核，所以是 42.7%；内存 1,220,608 字节；数据卷 12 字节 |
| 按实时 CPU 排序 | `total` 1，这个容器排第一 |
| `env_stop` 之后 | CPU、内存为 `not_running`（不是 0）；数据卷仍是 ok，12 字节 |

同一场景里原有的检查也都通过：接入包原样安装，Docker 28.0.4 认得 runsc；gVisor 看到的内核是 4.19.0-gvisor，宿主是 7.0.0-1017-azure；出口代理放行的域名返回 200、其他域名 403、直连外网不通；会话环境按 `issue_closed` 删除；常驻环境停止后再执行自动启动；归档后从快照重建；删除后记录保留。

`runner_browser.cjs` 的输出（原文）：

```
通过  容器服务器页没有服务器时引导到“服务器设置”（地址里记着 ?tab=settings），添加容器服务器（外网入口），出现“通过 SSH 部署”
通过  读取主机公钥（不登录）：页面显示的 SHA256:… 与 sshd 的一致；勾选核对后确认指纹；390px 宽度无横向滚动
通过  输入一次密码开始部署；页面跟着任务显示 6 步（连接、核对、专用密钥、上传接入包、安装脚本、上线）直到“部署完成，容器服务器已连上工作台”
通过  容器总览：主机条显示在线、gVisor 可用与最近联系；四个统计卡；还没有容器时如实说明；390px 宽度无横向滚动
通过  旧链接 ?tab=environments 换到容器总览并保留筛选；详情直链遇到不存在的容器显示“不存在或已无权访问”
通过  智能体用量、环境模板、执行记录标签都能打开（地址里记着 ?tab=，刷新后不变），没有加载失败；390px 宽度无横向滚动
通过  服务器设置：回收策略显示生效值，保存成功
通过  卸载（同时删除接入包目录，sudo 再填一次密码）：页面显示每一步直到卸载完成，runner-agent 容器已删除
通过  吊销专用密钥、删除 SSH 地址、移除容器服务器，回到“添加容器服务器”
通过  安全：密码只随两个请求发出，不在页面、91 个接口响应与浏览器存储里；121 个请求都指向工作台

全部通过（页面无错误）
```

这次 CI 修好了三个问题：

- 接入包漏带 `metrics.py`，场景 26 超时。接入包的模块清单补上它，并加了单元测试：`test_bundle_carries_every_agent_module`。
- `runner_browser.cjs` 用 `isVisible()` 取瞬时状态，页面还没渲染完就判断了。改成等元素出现。
- 切标签时只认 `?tab=` 在开头的地址；地址里还带着别的参数时等不到。改成认任意位置的 `tab` 参数。

之前一次运行里，api 作业的 `test_host_metrics_api` 因计时偶发失败；这与本 PR 无关，已在 PR 评论里说明。2fde6cd 上 api 作业一次通过。

