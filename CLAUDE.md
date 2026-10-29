@AGENTS.md

## Claude 专属说明（由 claude 维护）

- 你的协作身份是 `claude`：所有命令写 `python3 scripts/collab.py <命令> --as claude`。
- 云端会话会分配一个 `claude/...` 分支，它就是你本次的工作分支；`start`/`handoff` 会自动把它记进任务的 `branch` 字段。
- 人类说“按收工流程”或“提 PR”时，就可以创建 PR：标题 `[T-xxxx] 简述`，正文按 PR 模板填写。
- 被要求审查 Codex 的 PR 时，把意见作为 PR 评论留在对应行上，不要往 Codex 的分支推送。
