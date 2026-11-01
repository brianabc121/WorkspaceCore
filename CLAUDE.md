@docs/AGENTS.md

## Claude 专属说明（由 claude 维护）

- 你的协作身份是 `claude`：所有命令写 `python3 scripts/collab.py <命令> --as claude`。
- 云端会话会分配一个 `claude/...` 分支，它就是你本次的工作分支；`start`/`handoff` 会自动把它记进任务的 `branch` 字段。
- 完成任务后就创建 PR：标题 `[T-xxxx] 简述`，正文按 PR 模板填写；之后订阅这个 PR 的事件等 CI。
- 合并按 `.collab/decisions/D-0004-auto-merge.md`：最新提交上所有检查成功、没有冲突、没有未处理的阻塞意见，就用 merge commit 合并，
  不必再问人类；第 3 节列出的例外情况仍要先问。合并后确认 main 上的工作流也成功。
- Codex 的 PR：合并前先审查，意见作为 PR 评论留在对应行上，不要往 Codex 的分支推送；有阻塞问题就不合并，交还 Codex。
- Docker 只用本会话自己的环境，不与 codex 共用同一个 Docker daemon（D-0006）；不停止或删除不认识的容器、网络与数据卷。
- 给 codex 的指令写进 GitHub issue #50（“给 Codex 的指令”）并保持更新：新增、改状态、完成后划掉（人类 2026-10-31 要求，免得人类手动转发）。
  任务与球权仍以 `.collab/` 为准；issue 只是传话。
- **codex 暂停中（D-0007，2026-10-31 起）**：所有任务由 claude 独立完成，包括 `apps/web`；issue #50 已标为暂停。
  前端改动合并前跑 `apps/web` 的 lint / typecheck / test / build / check:api 和相关浏览器验收。人类恢复 codex 后按新的决策执行。
- 人类要求用中文回复。
- 本会话自己的 Docker 守护进程（后台任务最长 2 小时）到时限停止后，直接重启继续工作，不必再问人类（人类 2026-11-01 同意）。
  仍然只用自己的守护进程（D-0006），不动不认识的容器、网络与数据卷。
