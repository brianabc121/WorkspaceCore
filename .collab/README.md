# 协作中心：Claude ↔ Codex 交接方案

这份文档给**你（人类）**看：方案怎么运作、你每天要做什么、遇到问题怎么办。
代理必须遵守的规则写在 [`docs/AGENTS.md`](../docs/AGENTS.md)（仓库根目录的 `AGENTS.md` 是指向它的链接，供 Codex 自动读取）。

## 一句话

**仓库里的文件就是看板。** 每个任务一个文件，`owner` 表示“球在谁手里”；
球权只能通过一张写满内容的**交接单**转移；所有状态变化跟代码一起走 PR，**合并进 main 后生效**。
按 [D-0008](decisions/D-0008-stable-pr-integration.md) 并行开发、固定 PR 提交，由一个登记的集成会话按队列处理；
默认角色为 Claude，Codex 的 PR 默认先经 Claude 审查。首页规则见 [README](../README.md)，
通知、编号预留和人工队列见 [issue #235](https://github.com/brianabc121/WorkspaceCore/issues/235)。

```mermaid
flowchart LR
    H([你 human<br/>派活 · 裁决 · 审批])
    C[Claude]
    X[Codex]
    M[(main 分支<br/>.collab/ 看板)]
    X -- "PR：代码 + 任务状态 + 交接单" --> Q[准备就绪队列]
    C -- "PR：固定验证提交" --> Q
    Q --> I[唯一登记的集成会话]
    I -- "候选与相关检查通过 → 合并" --> M
    I -. "尚未授权的人类例外" .-> H
    M -- "开工：inbox" --> C
    M -- "开工：inbox" --> X
```

任务与正式交接以 main 上的文件为准；通知、编号预留和人工集成窗口记录在 #235，不替代 owner / status。
所有会话开工和准备合并前读取通知，避免依赖人类手工转发。这样做有三个好处：

1. 两边都没有跨会话记忆，文件是唯一可靠的记忆；
2. 仓库文件提供持久状态；GitHub 连通性以当前实测为准，无法读取 #235 时仍可做已指派的开发，但不能凭过期记录领取集成窗口或分配新编号；
3. 合并前由唯一集成者确认相关必需验证成功和审查要求满足；默认审查安排及本次人类授权的纯文档启动发布例外均按 D-0008，正式交接仍随 main 生效。

## 目录

```
AGENTS.md                  → docs/AGENTS.md 的链接（Codex 只自动读取根目录这份，不要删）
CLAUDE.md                  引用 docs/AGENTS.md + Claude 专属说明（Claude 自动读取）
docs/AGENTS.md             所有代理的规则（协作协议 + 项目规则）
docs/decisions/            技术决策 ADR（含前后端接口契约）
.collab/
  README.md                本文件
  tasks/T-0001-xxx.md      任务：头部字段 = 状态，正文末尾 = 进度日志
  handoffs/                交接单：<时间>_<任务>_<from>-to-<to>.md，只增不改
  decisions/D-0001-xxx.md  决策记录（为什么这么做）
  templates/               任务 / 交接单 / 决策模板
scripts/collab.py          命令行工具（Python 标准库；inbox 可调用 git fetch）
scripts/collab_sync.py     CI 专用：把状态镜像到 GitHub Issues
.github/
  pull_request_template.md PR 模板
  workflows/collab.yml     CI：校验 .collab/，输出看板，预览合并后的同步
  workflows/collab-sync.yml main 更新时 / 每天：交接提醒 issue + 看板 issue
```

## 核心概念

### 任务文件的头部

```yaml
id: T-0003
title: 实现登录接口
owner: codex          # 球权：claude | codex | human | unassigned
status: in_progress   # todo | in_progress | handoff | blocked | done | cancelled
priority: P1          # P0（最急）~ P3
branch: codex/login   # 最近的工作分支
paths: src/api/auth   # 本任务会改的目录/文件，用于冲突检测
depends_on: T-0001
```

### 状态机

```mermaid
stateDiagram-v2
    [*] --> todo: new
    todo --> in_progress: start
    in_progress --> handoff: handoff → 另一个代理
    handoff --> in_progress: 接收方 start
    in_progress --> blocked: handoff → human（escalate）
    blocked --> handoff: 你决定后交给某个代理
    in_progress --> done: set --status done
    todo --> cancelled
    in_progress --> cancelled
    done --> [*]
```

### 交接单的 8 个固定部分

1. 一句话摘要
2. 已完成（附证据）
3. 交给对方的工作和完成标准（勾选框）
4. **如何验证当前状态**（接收方开工前先跑）
5. 关键决策与理由
6. 已知问题 / 风险 / 坑
7. 不要改动 / 必须保持的约定
8. 相关文件与入口

任何一节留着“（待填写”，`check` 和 CI 都会失败，所以不会出现空的交接单。

## 你的日常操作

### 让代理开工（复制即用）

**Claude**（Claude Code 云端会话，选这个仓库）：

> 先读 main 上的 README / D-0008 和通知队列 #235，再按 AGENTS.md 看 inbox、处理已指派任务。准备就绪的 PR 按队列集成；不要因 main 无关推进改变正在验证的 PR head。

**Codex**（Codex 云端任务，选这个仓库的 **main** 分支）：

> 你的协作身份是 codex。先 fetch 并只读 main 上的 README / D-0008 和通知队列 #235，再按 AGENTS.md 查看 inbox、处理已指派任务。开新任务从最新 main 开始，已有验证候选不机械同步 main。

要指定任务就加一句“处理 T-0003”。Sprint 0 前端的开工指令是 `docs/CODEX_START.md`，把全文发给 Codex 即可（对应协作任务 T-0004）。

交接合并进 main 后，GitHub 会自动开一个 `[交接]` issue（你会收到通知），里面就有给接收方的这段话，直接复制即可。

### 其他常用说法

| 你想要 | 对代理说 |
|---|---|
| 拆需求 | “先写任务摘要、验收标准、owner 与 paths，提交到 #235 请唯一集成者预留编号并按模板创建正式任务，先不要实现。” |
| 指派任务 | “把 T-0005 指派给 codex。”（或自己运行 `collab.py set T-0005 --as human --owner codex -m 指派`） |
| 交叉审查 | 在 Claude 的 PR 下评论 `@codex review`；或对 Claude 说“审查 PR #N，把意见留在 PR 上” |
| 处理上报 | 看 `[交接] … → human` 的 issue 和它链接的交接单，决定后对某个代理说“T-0007 按方案 B 做，交给你了”（代理会先记录这次指派再开工） |
| 看进度 | 打开 GitHub 上的「📋 协作看板」issue（自动更新）；或运行 `python3 scripts/collab.py status` |

### 合并 PR

平时按 [D-0008](decisions/D-0008-stable-pr-integration.md) 执行：

1. 准备就绪的 PR 在 #235 登记链接、head SHA、类别、检查结果和关联风险。
2. 唯一登记的集成会话按就绪顺序领取窗口；候选验证期间其他 PR 不插队，功能分支可继续开发。
3. 在独立集成分支核对最新 main 与 PR，检查代码及编号 / 迁移 / 契约等语义冲突，按实际改动选择验证。
4. 相关检查成功、无阻塞审查意见后用 merge commit 发布，核对 main 和必要检查，再释放窗口。
5. 失败或中断时记录并释放窗口；作者修好后重新满足入队条件。不得忽略相关失败或伪造通过。

纯文档 / 元数据需要协作、链接 / 格式、密钥与范围检查；人类授权的规则修改还要独立复核。
业务、共享依赖、迁移、安全及关联不确定的变更按 D-0008 扩大验证。main 无关推进不自动触发功能分支同步；
同一 SHA 的既有成功检查保留，已确认的环境瞬态失败只重跑失败 job。平台已启用的额外保护仍须满足。

正式任务号由唯一集成者在 #235 预留后分配；现有 `collab.py new` 不提供原子占号。
已有任务不追溯改号，未分配时先在 issue 提交摘要 / owner / paths；工具改造前由集成者按模板创建正式任务。

当前先人工执行，自动占号、变更分类、`ci-gate`、过期运行取消和自动队列单独实施。
本次规则启动发布由 Codex 完成，之后由 Claude 登记唯一集成会话；“Claude 负责集成”不表示每个 Claude 会话都能同时合并。

未授权修改人类负责的规范 / 开工指令或协作规则、不可逆操作、进入下一个 Sprint 仍须人类决定；已明确批准的同一事项不重复询问。

想在 Claude 的 PR 合并前让 Codex 再看一遍，在 PR 下评论 `@codex review`。

## 命令速查

```bash
python3 scripts/collab.py status                      # 看板（按 owner 分组）
python3 scripts/collab.py inbox   --as codex          # 某个身份的待办（会尝试 fetch 远端）
# 新建正式任务：先在 #235 申请编号，由唯一集成者预留后按 .collab/templates/task.md 创建。
# collab.py new 目前不支持原子占号，不作为并行新建命令示例。
python3 scripts/collab.py start   T-0003 --as codex   # 开始 / 接手交接
python3 scripts/collab.py handoff T-0003 --as codex --to claude --type continue -m "一句话摘要"
python3 scripts/collab.py set     T-0003 --as codex --status done -m "验收标准已全部满足"
python3 scripts/collab.py log     T-0003 --as claude "在 PR 上提了两条审查意见"
python3 scripts/collab.py decision --as claude --title "API 统一用 REST"
COLLAB_AGENT=codex python3 scripts/collab.py check     # 校验（CI 同款；check 无 --as 参数）
```

任务号可以简写：`3`、`T-3`、`T-0003` 都可以。

## 分工

长期目录参考见 [D-0003](decisions/D-0003-module-ownership.md)；当前已有任务和人类明确派活的 owner / paths 优先，
本次恢复并行不自动转移任务或目录。合并权限统一按 D-0008 的唯一登记会话执行。

| 谁 | 负责 |
|---|---|
| Codex | 前端 `apps/web`、`ci-web.yml` |
| Claude | 后端 `services/api`、数据库迁移、Compose / Caddy、根目录文件、第三方 API 核验、联调与验收；默认集成角色由一个登记会话承担 |
| 你 | 需求与路线图、开工指令、例外情况的决定、批准进入下一个 Sprint |

每个 Sprint：Claude 先提交接口契约（OpenAPI）→ 两边并行实现 → Claude 联调验收并勾选 `docs/TASKS.md` → 你批准进入下一个 Sprint。
Codex 的 PR 默认由 Claude 审查；Claude 的 PR 按人类要求安排交叉审查。本次人类授权的规则发布按 D-0008 采用独立复核。
要正式调整长期目录归属，写新的决策；不把目录参考表当作自动接管现有任务的依据。

## 常见问题

**对方的交接还没合并，能先开始吗？**
默认不行，合并后才生效。`inbox` 第 7 节会预告“尚未合并的交接”。确实着急时，
你可以让接收方从发出方的分支开工，接收方的 PR 会包含两边的提交，原 PR 关闭即可。

**两个代理同时干活会冲突吗？**
`paths`、inbox 与 check 用于发现重叠，但不同文件也可能出现编号、迁移或契约等语义冲突。
由集成者在独立分支生成合并候选、解决冲突并补验受影响范围；不默认让作者把 main 合回正在验证的 PR。

**任务号撞了怎么办？**
`new` 扫描本机已知远端分支后取最大号加一，不是原子占号，仍会并发撞号。新任务先由唯一集成者在 #235 预留并按模板创建。
已在 main 登记的正式任务保持不动；历史待合并分支中尚未入主干的新增任务若冲突，由集成者在最终候选里消歧、记录映射并保留历史日志，
只补相关元数据检查，不让作者为改号推送改变产品输入之外的无意义提交，也不全套重跑无关业务测试。

**两个代理意见不一致？**
都不许直接推翻对方。各自写决策提案或在日志里说明理由，escalate 给你，你拍板后改成 accepted。

**代理没按流程走？**
CI 的 `collab` 检查会拦住大部分格式问题（空交接单、状态不一致、编号重复）。
流程上的问题（比如越界改代码）在审查 PR 时指出，并让它在日志里补记录。

**想改协议本身？**
你直接改，或让代理写一个 `proposed` 决策，你批准后再改 `AGENTS.md` / `collab.py`。

## GitHub 上的自动化

`collab-sync` 在 main 每次更新时、以及每天北京时间 09:17 运行（`scripts/collab_sync.py`）：

| 自动做的事 | 说明 |
|---|---|
| 交接提醒 issue | main 上每出现一张新交接单，开一个 `[交接] T-xxxx from → to` 的 issue（标签 `collab-handoff`），里面有摘要、要做的事和**给接收方的开工话术** |
| 自动关闭提醒 | 接收方 `start` 之后（任务不再停在这张交接单上），自动评论并关闭对应提醒 |
| 看板 issue | 维护一个「📋 协作看板」issue（标签 `collab-board`），按 Claude / Codex / 等人类 分组显示所有任务；可以把它置顶 |
| 停滞提醒 | 进行中 / 交接中 / 阻塞超过 3 天没更新的任务，在看板上标 `⚠ N 天未更新` |

这些 issue 只是镜像，事实来源始终是 `.collab/` 下的文件；手动改 issue 不会改变任务状态。
每个 PR 的 `collab` 检查里还有一步“预览合并后的 GitHub 同步”，可以在合并前看到会开哪些提醒。

不用 GitHub Projects：用户名下的 Projects 需要额外配置带权限的令牌，看板 issue 不需要任何配置。

### 建议你在 GitHub 上手动做的设置（可选）

- **Settings → Branches → 为 `main` 添加保护规则**，勾选 “Require status checks to pass”，选中 `check`：
  这样 `collab` 检查不通过的 PR 就合并不了。
- 把「📋 协作看板」issue 置顶（issue 页面右侧 Pin issue）。
