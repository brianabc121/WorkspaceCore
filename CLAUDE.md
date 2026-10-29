@docs/AGENTS.md

## Claude 专属说明（由 claude 维护）

- 你的协作身份是 `claude`：所有命令写 `python3 scripts/collab.py <命令> --as claude`。
- 云端会话会分配一个 `claude/...` 分支，它就是你本次的工作分支；`start`/`handoff` 会自动把它记进任务的 `branch` 字段。
- 完成任务后就创建 PR：标题 `[T-xxxx] 简述`，正文按 PR 模板填写；之后订阅这个 PR 的事件等 CI。
- 合并按 `.collab/decisions/D-0004-auto-merge.md`：最新提交上所有检查成功、没有冲突、没有未处理的阻塞意见，就用 merge commit 合并，
  不必再问人类；第 3 节列出的例外情况仍要先问。合并后确认 main 上的工作流也成功。
- Codex 的 PR：合并前先审查，意见作为 PR 评论留在对应行上，不要往 Codex 的分支推送；有阻塞问题就不合并，交还 Codex。
- 人类要求用中文回复。
