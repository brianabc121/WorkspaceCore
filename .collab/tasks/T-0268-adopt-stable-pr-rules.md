---
id: T-0268
title: Claude 接收并执行稳定 PR 与分层验证协作规则
owner: claude
status: done
priority: P1
branch: claude/gracious-euler-ldkldx
paths: .collab/tasks/T-0268-adopt-stable-pr-rules.md
depends_on: T-0267
created: 2026-11-16
updated: 2026-11-16
---

# T-0268 Claude 接收并执行稳定 PR 与分层验证协作规则

## 目标
按人类“告知 Claude 按该方案执行”的要求，读取已批准的 D-0008 与首页规则，确认已读，在通知 / 人工集成队列 #235 登记唯一集成会话或当前排队状态，并在后续工作中执行固定 PR 提交、统一集成和分层验证。

## 验收标准
- [x] 读取 main 上的 README、AGENTS、CLAUDE、D-0008 和本任务交接单。
- [x] 在 issue #235 回复已读与会话 / 分支标识，核对是否已有集成会话；有现任时保持排队，不自行并行接管。
- [x] 维护 issue #50 时更新过时的 Codex 暂停说明，保留已有任务与历史；后续 PR 按新流程执行。
- [x] 确认人工规则与尚未实现的自动化边界；工具改造另开任务，记录完成本次接收确认。

## 背景 / 约束 / 参考
人类已在当前对话明确批准并要求通知 Claude。D-0008 取代 D-0004 的合并 / 门禁规则及 D-0007 的暂停假设；已有任务不自动转移 owner。仅本次规则发布由 Codex 完成启动集成，之后按 #235 登记一个 Claude 会话。工具与 workflow 本次没有改造。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 16:58 UTC · codex · 创建任务（owner: codex）
- 2026-11-16 16:58 UTC · codex · 开始任务
- 2026-11-16 16:58 UTC · codex · 交接给 claude（continue）→ .collab/handoffs/2026-11-16-1658_T-0268_codex-to-claude.md：人类要求告知 Claude 按 D-0008 执行：读取首页规则与通知 #235，确认已读并登记唯一集成会话，后续按固定提交、排队集成与分层验证处理 PR
- 2026-11-16 17:37 UTC · claude · 接手交接 2026-11-16-1658_T-0268_codex-to-claude.md
- 2026-11-16 17:38 UTC · claude · 已读 main 614b7b8 上的 README 首页协作规则、docs/AGENTS.md、CLAUDE.md、D-0008、本任务交接单与 #235，交接单第 4 节的验证（D-0008 accepted、CLAUDE 引用新规则、T-0268 owner claude / handoff、collab check 通过）都符合预期。在 #235 回复“已读并按 D-0008 执行”，并登记本会话（claude session_0171QGWLzYfTCvXBmKt9H2Yq / 分支 claude/gracious-euler-ldkldx）为唯一集成会话：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6923727256 （登记前窗口空闲，没有别的会话登记）。按 D-0008 处理的第一个候选是 PR #234（T-0269，固定 head、预留编号、在最终合并提交里改号、按改动类别验证），合并为 c6d0473。issue #50 顶部的暂停说明改成“已恢复并行（D-0008 取代 D-0007）”，开工步骤加上读 D-0008 与 #235、在 #235 申请编号和排队，任务号写法改成在 #235 预留；C-8、C-3、C-6 与“已完成”原样保留。确认边界：唯一 ID / 原子占号、ci-gate、过期运行取消、自动集成队列都没有实现，现在是人工流程，工具改造另开任务；本任务不声称这些已上线。
- 2026-11-16 17:38 UTC · claude · status in_progress → done：已读并按 D-0008 执行：#235 已确认并登记唯一集成会话，issue #50 的暂停说明已更新
