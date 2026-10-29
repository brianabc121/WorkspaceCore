<!-- 标题格式：[T-xxxx] 简述；不建任务的小改动用 [chore] 简述 -->
<!-- 正文结构 = AGENTS.md 第 8 节“提交格式” + 协作信息 -->

## 当前 Sprint / 已完成任务
- 协作任务：T-xxxx
- `docs/TASKS.md` 条目：S?-??（已在本 PR 中勾选）
- 作者：claude / codex / human
- 本 PR 合并后：status = ，owner =
- 交接单：无 / `.collab/handoffs/...`

## 变更文件
-

## 实现决策及偏差
<!-- 与 DEVELOPMENT_SPEC 不一致的地方、新写的 docs/decisions/ ADR -->

## 实际执行的命令和结果
<!-- 原样贴出命令与输出摘要；没能运行的写明原因，不得虚构通过结果 -->

## 安全 / 数据迁移影响

## 遗留问题与下一步

## 请求审查
<!-- 希望谁审：codex / claude / human；需要重点看的地方 -->

## 自检
- [ ] `python3 scripts/collab.py check` 通过
- [ ] 相关 lint / typecheck / tests / build / migrations 已运行（`make lint`、`make test` 等），结果如实记录在上面
- [ ] 只改了任务 `paths` 范围内的文件（越界的已在任务日志里说明）
- [ ] 没有提交密钥、真实用户数据、`node_modules`、虚拟环境
- [ ] 任务进度日志已更新；交接单（如有）没有留下“（待填写”
