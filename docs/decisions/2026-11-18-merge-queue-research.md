# 合并主干的业内做法调研（2026-11-18）

> 用途：给 [D-0009](../../.collab/decisions/D-0009-batch-integration-train.md)（批量“车次”集成，人类 2026-11-18 已批准）和
> [版本更新执行手册](../guides/integration-runbook.md)提供依据。
> 起因：人类 2026-11-18 说会有十几个开发好的需求排队合并，前面的 CI 慢，后面的都合不进去，
> 要求“参考下业内专家他们是怎么做的”。

## 1. 本仓库现状（按 #235 记录与 Actions 实际耗时）

| 项目 | 数据 |
|---|---|
| 每个 PR 推送一次启动的 job | 约 18 个（ci-api 1、ci-infra 8、ci-llm-bridge 3、ci-security 3、Web CI 1、collab 1、Runtime network image 1） |
| 最慢的 job | `ci-api / api` 约 56 分钟，其中 pytest 一步 55 分钟（main `a39bf92`：11:04:16 → 11:59:07）；`ci-infra / agents` 约 50 分钟（其中多实例端到端 22 分钟、SSH 验收 8 分钟、OpenIM 镜像构建 6 分钟）；`ci-infra / compose` 约 16 分钟 |
| pytest 慢的原因 | 每个集成测试都新建一个数据库，再从头跑全部 Alembic 迁移（`services/api/tests/conftest.py` 的 `temp_database_url` + `migrated_database_url`，测试里有 436 处用到这组夹具）；单进程运行 |
| 合并后到释放窗口 | 2026-11-16 ~ 11-18 的 10 次：52、52、56、57、68、69、70、72、130、148 分钟，中位数约 68 分钟。集成会话每次都等 main 的推送检查全部跑完才释放窗口 |
| 这些等待里有多少是重复测试 | #250、#255、#256、#257 合并时，PR 的基线就是当时的 main，合并后 main 的内容和 PR head 完全相同，那段时间是在重复测同一份代码 |
| 偶发失败 | 一天里“测试夹具连 CI 的 Redis 超时”至少 5 次；另有 A16 补发事件的时序、dashboard 并发测试的 1 秒时限、agents 的用量上报。每次重跑再加约 1 小时，期间整条队列停着 |
| 吞吐上限 | 一次一个 PR、每个约 1 ~ 2.5 小时：12 个 PR 需要 12 ~ 24 小时 |

## 2. 业内做法

### 2.1 “不是火箭科学”规则与 bors（Rust）

- Graydon Hoare（Rust 的作者）2014 年写的 “The Not Rocket Science Rule Of Software Engineering”：
  **自动维护一个总能通过全部测试的代码仓库**。做法是机器人 bors 在 PR 获批后，
  先把 PR 和当前主干合成一个临时提交去测，测过了才把这个提交推进主干。
- 他同时指出：集成周期受测试周期限制，有的改动要试几次才能合进去。
- Rust 的补充做法是 **rollup**：把若干个已获批的小 PR 打包成一个 rollup PR，测一次一起合入；
  审批时可以标记某个 PR 不能进 rollup（例如 `rollup=never`）。rollup 里任何一个 PR 被撤销批准，整个 rollup 作废。
- 后来 bors 的思路被各家合并队列继承。Jane Street 的文章评价：这些技巧“改变的是常数，不是渐近复杂度”，团队规模再大十倍、百倍，还是要换办法。

来源（2026-11-18 核对）：[Hoare 原文](https://graydon2.dreamwidth.org/1597.html)（读的是[转载](https://eluvk.github.io/readings/nrsr.html)）、[Rust bors 队列](https://bors.rust-lang.org/queue/rust)、[rollup 示例](https://github.com/rust-lang/rust/pull/160654)、[Jane Street](https://blog.janestreet.com/making-never-break-the-build-scale/)。

**对我们的意义**：D-0008 的“固定候选、测过再合”就是这条规则；缺的是 rollup 式的批量。

### 2.2 GitHub 原生合并队列（merge queue）

- 机制：PR 加入队列后，GitHub 建一个临时分支，内容是“最新的基础分支 + 排在它前面的 PR + 它自己”，
  在上面跑必需检查；通过后合入基础分支。前面的 PR 被移出队列时，后面的临时分支会重建。
- 设置项：构建并发数（1 ~ 100，同时测几个临时分支）、最小 / 最大分组大小（一次合几个 PR）、
  等待时间（凑不满最小分组时最多等多久）、状态检查超时。工作流必须加 `merge_group` 触发事件才会在队列分支上运行。
- 失败处理：分组里失败的 PR 被移出队列，后面的重新组合再测。
- **可用范围**：组织拥有的公开仓库，或使用 GitHub Enterprise Cloud 的组织私有仓库。
  **个人账号的仓库不能用**（与 D-0008 第 6 节的判断一致；多个第三方仓库记录了 API 拒绝 `merge_queue` 规则）。
  本仓库目前属于个人账号，要用就得先迁到组织下，由人类决定。

来源（2026-11-18 核对）：[About merge queues](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-queues)、[Managing a merge queue（GHES 3.20）](https://docs.github.com/en/enterprise-server@3.20/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)；个人账号不可用的说法另见第三方记录，例如 [evgunter/cad#1887](https://github.com/evgunter/cad/pull/1887)。

**对我们的意义**：“测的就是要合的”加“分组批量”，是我们手工车次要模仿的对象；以后迁到组织仓库可以直接换成它。

### 2.3 Zuul（OpenStack）的推测执行

- Zuul 的“依赖型流水线”（dependent pipeline）假设队列里前面的改动都会通过，
  **同时**把每个改动和它前面所有改动叠在一起测（推测执行）。
- 某个改动失败：把它移出，排在它后面的改动全部丢弃已跑的结果、去掉它重新测。
- 有一个“窗口”限制同时推测的改动数（文档说默认从 20 开始，按类似 TCP 的算法伸缩）；
  每个项目有自己的队列和窗口，一个项目的偶发失败不会拖住别的项目。

来源（2026-11-18 核对）：[Zuul Project Gating](https://zuul-ci.org/docs/zuul/latest/gating.html)。

**对我们的意义**：推测执行需要大量并行的 CI 资源；个人账号同时运行的 job 数有限（每个 PR 已经 18 个 job），现阶段不采用。
“失败只剔除那一个、其余重组”可以直接采用。

### 2.4 Uber SubmitQueue（EuroSys 2019，“Keeping Master Green at Scale”）

- 目标：主干上每个提交都通过全部构建步骤。上线前 iOS 主干只有 52% 的时间是绿的。
- 做法：为排队中的改动建“推测图”，用模型估计每个改动通过的概率，优先构建最可能通过的组合；
  **冲突分析器**判断哪些改动互不相关，互不相关的可以并行构建、各自合入。
- 2025 年的后续论文（arXiv 2501.03440）加上了长短构建区分和推测阈值，减少浪费的构建。

来源（2026-11-18 核对）：[Uber 论文页](https://www.uber.com/blog/research/keeping-master-green-at-scale)、[论文摘要](https://blog.acolyer.org/2019/04/18/keeping-master-green-at-scale/)、[2025 年后续](https://arxiv.org/abs/2501.03440)。

**对我们的意义**：“互不相关的改动不必串行”在我们这里体现为“同一车可以装多个 PR”；
模型预测和冲突分析是大规模才需要的，不采用。

### 2.5 Google TAP：提交前快测，提交后批量全测

- 提交前（presubmit）只跑受影响的、较快的测试；提交后（postsubmit）把一段时间内的提交合成一批全量测试
  （论文里称为里程碑 milestone），失败时由**自动找罪魁**（culprit finding）定位是哪个提交。
- 偶发测试：2016 年 Google 测试博客报告，约 1.5% 的测试运行结果是“偶发”（同一份代码有时过有时不过），
  约 16% 的测试有过偶发；提交后测试里约 84% 的“由过变不过”和偶发测试有关。
  缓解办法包括只重跑失败的测试、失败后自动重跑。
- 后续论文 “Flake Aware Culprit Finding” 说明：找罪魁时要考虑偶发，否则二分会被误导。

来源（2026-11-18 核对）：[Taming Google-Scale Continuous Testing](https://research.google/pubs/taming-google-scale-continuous-testing/)、[Google 测试博客 2016-05](https://testing.googleblog.com/2016/05/)、[What Breaks Google?](https://research.google/pubs/what-breaks-google/)。

**对我们的意义**：偶发失败是吞吐的头号敌人，必须当成缺陷来修；我们的规则禁止跳过测试，所以采用“只重跑失败 job 一次 + 立即开任务修”。
“提交后批量全测 + 找罪魁”的思路对应我们的车次失败后二分。

### 2.6 Chromium 提交队列（CQ）

- “试跑”（dry run）只报告结果、不合并；“正式提交”跑同样的测试，没有引入新的失败就合入。
- 某个测试套件失败后，CQ 会**带补丁重跑一次、不带补丁再跑一次**：不带补丁也失败，说明问题在主干、不怪这个改动；
  只有带补丁才失败，才算这个改动引入的。

来源（2026-11-18 核对）：[Chromium CQ 文档](https://chromium.googlesource.com/chromium/src/+/main/docs/infra/cq.md)。

**对我们的意义**：归因时先看“同一个测试在 main 上是否也失败过”。我们的偶发清单就是简化版的“不带补丁也会失败”。

### 2.7 Shopify：合并队列与限量批次

- Shopify 的主应用是一个大型 Rails 单体，合并队列做在它的部署工具 Shipit 里。
- 第一版队列里的 PR 不跑 CI，出事故时会积压 50 个以上的 PR，要好几个小时才能合完、部署完。
- 第二版让 CI 在队列里跑，失败的 PR 移出队列，避免拖累后面的；同时限制每批部署的提交数（二手资料说每次 5 ~ 10 个），
  出问题时容易定位和回滚。后来与 Graphite 合作，按仓库区域把队列分成几个“虚拟队列”（厂商案例，数字仅供参考）。

来源（2026-11-18 核对）：[Successfully Merging the Work of 1000+ Developers](https://shopify.engineering/successfully-merging-work-1000-developers)、[Introducing the Merge Queue](https://shopify.engineering/introducing-the-merge-queue)；每批 5 ~ 10 个和 Graphite 的数字是二手资料。

**对我们的意义**：每车设上限，失败只剔除问题 PR；仓库再大时可以按区域分队列（例如 llm-bridge 与 core 分开），现阶段不需要。

### 2.8 商业合并队列（Mergify、Trunk、Aviator、Graphite）的共同点

- **批量**：把多个 PR 当成一个单元测一次；批量失败后二分或逐个剔除。
- **按影响范围并行**：互不影响的区域各走各的队列。
- 分组不改变优先级和排队顺序。

来源（2026-11-18 核对）：[Trunk 批量](https://docs.trunk.io/merge-queue/concepts/batching)、[Mergify 批量](https://docs.mergify.com/merge-queue/batches)。

### 2.9 CI 自身的常规做法

- **同一 PR 推新提交时取消旧的运行**：GitHub 文档给出的写法是
  `concurrency: group: ${{ github.workflow }}-${{ github.head_ref || github.run_id }}`、`cancel-in-progress: true`；
  `head_ref` 只在 pull_request 事件里有值，main 的推送会落到各自的 `run_id`，不会被取消。
- **按路径跳过与必需检查**：整个工作流被路径过滤跳过时，对应的必需检查会一直停在 Pending；
  工作流运行了、而 job 被 `if:` 跳过时算成功。所以做法是一个**始终运行**的总检查（D-0008 里叫 `ci-gate`），
  里面按改动类别判断需要哪些 job，并用 `needs` + `always()` 读它们的结果，不能用工作流级的 `paths` 跳过必需检查。
- **测试数据库**：迁移只跑一次，存成模板库，每个测试用 PostgreSQL 的 `CREATE DATABASE … TEMPLATE …` 复制一份；
  多进程（pytest-xdist）时每个进程各用一个模板名和 Redis 库号。有资料提醒：测试量很大时模板复制本身也有开销，要实测。

来源（2026-11-18 核对）：[Control workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)、[Troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)、[Mergify：每个测试一个数据库](https://mergify.com/blog/postgres-database-per-test-sqlalchemy)、[pgtestdb](https://github.com/peterldowns/pgtestdb)。

## 3. 共同原则

1. **测的就是要合的**：合入主干的内容必须和测过的内容完全一样（bors、GitHub 合并队列、Zuul 都是这样）。
2. **批量摊薄 CI 时间**：一次测多个 PR；失败了剔除或二分，不让一个坏 PR 拖住整批。
3. **失败要隔离**：失败的移出，其余继续；不同区域可以各走各的队列。
4. **偶发失败当缺陷修**：它直接决定吞吐；重跑只能作为临时手段，并要区分“主干本来就会失败”和“这个改动引入的”。
5. **CI 本身要快**：集成周期受测试周期限制，缩短最长的那个 job 是根本办法。
6. **队列要自动化**：不依赖某个人或某个会话在线。

## 4. 对本仓库的取舍

| 做法 | 结论 | 原因 |
|---|---|---|
| 测的就是要合的 | 采用（D-0008 已有） | 继续保留 |
| 手工批量车次（仿 rollup / 合并队列分组） | **采用**，写进 D-0009 | 不需要新工具，立刻能把 12 个 PR 从 12 轮 CI 降到 2 ~ 3 轮 |
| 合并后内容与测过的相同就立即释放窗口 | **采用**，写进 D-0009 第 5 条 | 省掉每个 PR 约 1 小时的重复测试 |
| 失败剔除 / 二分 | 采用 | 同上 |
| 偶发失败：只重跑一次 + 立即开任务修 | 采用 | 规则禁止跳过测试，不能照搬“隔离偶发测试”的做法 |
| 同 PR 旧运行自动取消、始终运行的 `ci-gate` | 采用，另开工程任务 | D-0008 已列为后续工作 |
| pytest 用模板库与多进程、拆分 `agents` | 采用，另开工程任务 | 缩短最长的 job |
| 用 GitHub Actions 自动组车 | 以后再做 | 先把手工流程跑顺 |
| GitHub 原生合并队列 | 暂不采用 | 个人账号仓库不可用；迁到组织由人类决定 |
| 推测执行（Zuul）、预测模型（Uber） | 不采用 | 规模和 CI 并发额度都不需要 |

## 5. 核验方式与限制

- 2026-11-18 用网络搜索查到下列来源，读的是搜索结果给出的原文摘录。
- 本会话的网络规则不允许直接打开 docs.github.com、zuul-ci.org、graydon2.dreamwidth.org（请求在域名解析时被拦下），
  所以 GitHub 合并队列的设置项来自同一份文档的 GitHub Enterprise Server 3.14 ~ 3.20 版本；
  “个人账号仓库不能用合并队列”来自搜索摘录中引用的 GitHub 文档原句和多个第三方仓库的记录。
  要迁到组织仓库之前，应在 GitHub 当前文档上再核对一次。
- Shopify 每批 5 ~ 10 个提交、Graphite 的效果数字来自二手资料或厂商案例，只作参考。
- 本文不涉及第三方产品的接口实现，结论只用于协作流程。

## 6. 来源

- Graydon Hoare, The Not Rocket Science Rule Of Software Engineering（原文 <https://graydon2.dreamwidth.org/1597.html>；本次读的是转载 <https://eluvk.github.io/readings/nrsr.html>）
- Jane Street, Making “never break the build” scale：<https://blog.janestreet.com/making-never-break-the-build-scale/>
- Rust 的 bors 队列与 rollup：<https://bors.rust-lang.org/queue/rust>；rollup 示例 <https://github.com/rust-lang/rust/pull/160654>
- GitHub，About merge queues：<https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-queues>
- GitHub，Managing a merge queue（Enterprise Server 3.20 版）：<https://docs.github.com/en/enterprise-server@3.20/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue>
- GitHub，Troubleshooting required status checks：<https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks>
- GitHub，Control workflow concurrency：<https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency>
- Zuul，Project Gating：<https://zuul-ci.org/docs/zuul/latest/gating.html>（摘录来自 9.x 版本页面）
- Uber，Keeping Master Green at Scale（EuroSys 2019）：<https://www.uber.com/blog/research/keeping-master-green-at-scale>；
  摘要 <https://blog.acolyer.org/2019/04/18/keeping-master-green-at-scale/>；后续 <https://arxiv.org/abs/2501.03440>
- Google，Taming Google-Scale Continuous Testing（ICSE-SEIP 2017）：<https://research.google/pubs/taming-google-scale-continuous-testing/>
- Google 测试博客 2016 年 5 月（Flaky Tests at Google and How We Mitigate Them）：<https://testing.googleblog.com/2016/05/>
- Google，What Breaks Google?：<https://research.google/pubs/what-breaks-google/>
- Chromium，CQ 文档：<https://chromium.googlesource.com/chromium/src/+/main/docs/infra/cq.md>
- Shopify，Successfully Merging the Work of 1000+ Developers：<https://shopify.engineering/successfully-merging-work-1000-developers>；
  Introducing the Merge Queue：<https://shopify.engineering/introducing-the-merge-queue>
- Trunk，批量合并：<https://docs.trunk.io/merge-queue/concepts/batching>；Mergify，批量：<https://docs.mergify.com/merge-queue/batches>
- Mergify，每个测试一个 PostgreSQL 数据库（SQLAlchemy）：<https://mergify.com/blog/postgres-database-per-test-sqlalchemy>；
  pgtestdb（模板库复制）：<https://github.com/peterldowns/pgtestdb>
