# 任务的五种状态、个人查看与明确阻塞（T-0166 ~ T-0168）

- 状态：accepted（人类 2026-11-09 在对话中要求「按照效果图和设计文档进行全部落实」；方案 `docs/v2/pages-redesign/`）
- 范围：claude 自己任务范围内的数据与接口；不改协作协议、不放宽智能体工具。
- 取代：`docs/v2/HOME_PLAN.md` 第 2 节里“已完成待查看 = `review_pending`、阻塞 = 有未关闭的阻塞 Issue、进行中 = 开始过”的判定（T-0163）。

## 背景

首页改版（T-0163）给任务加了 `review_pending`：智能体完成时置真，owner / admin 点“确认结果”清掉。四页改造方案
（规范第 4.2、4.3 节）要求任务页、项目卡片、首页用同一套五种互斥状态，并且“查看”按人隔离：
用户 A 看过不能替用户 B 消掉提示；“阻塞”必须是有人写明的原因，不能从别的数据推断。

## 决定

1. **五种状态由服务端集中判定**（`app/domain/task_state.py`）。SQL 表达式 `work_state_expr(user_id)` 与 Python 的 `classify()`
   实现同一规则，列表筛选、状态计数、项目卡片、首页任务进展都用它：
   - 已完成：`done` 且（`completion_seq = 0` 或当前用户看过这一轮）→ `done`，否则 `awaiting_view`；
   - 未完成：有阻塞原因 → `blocked`；否则有未结束的执行段（`task_runs.ended_at IS NULL`）→ `running`；其余 → `todo`。
   - “开始过”（`started_at`）不再算进行中；暂停、中断也不自动算阻塞。
   读一页任务的状态固定 3 个查询（任务、执行中的段、当前用户的查看记录），不随条数增加（有测试）。
2. **完成轮次 + 个人查看记录取代 `review_pending`**：`tasks.completion_seq` 每次未完成→完成加 1；
   `task_result_reads` 每人每任务一行，记看过的最大轮次。本人手动完成时同一事务记本人已查看；
   智能体或别人完成时，所有人都进入待查看。重新打开不加轮次，再完成才加。编辑无关字段不加轮次。
   viewer 也能标记自己已查看（只写自己的记录）。删除 `POST /tasks/{id}/review` 与“确认结果”按钮。
3. **明确阻塞**：`tasks.blocked_at / blocked_reason / blocked_by`，`PATCH /tasks/{id}/block`（member，带 Idempotency-Key 与 version）。
   原因 1–2000 字、按纯文本显示。标记阻塞不停止正在执行的一段，解除也不自动重新开始；阻塞的任务不能被新领取或开始执行
   （`409 TASK_BLOCKED`），已完成的任务不能标阻塞（`409 TASK_DONE`），完成时清掉阻塞。
   首页不再用“有未关闭的阻塞 Issue”判定阻塞：Issue 仍可以讨论阻塞，但要不要算阻塞由人明确决定。
4. **历史任务不刷成未读**：迁移前完成的任务 `completion_seq = 0`，对所有人都是已完成；迁移 0051 里 `review_pending` 为真的任务
   记为第 1 轮，所有人都还没查看（与原来的“待确认”一致）。
5. **项目统计**：`ProjectOut.task_states`（五种状态计数，按当前用户）与 `last_activity_at`；`GET /projects/summary` 的“本周完成”
   由前端按浏览器时区算好时间窗（本周一 00:00 到下周一 00:00）传给服务端，统计 `done_at` 在窗口内、现在仍完成的任务。
   “需要关注” = 有你待查看或明确阻塞的任务。`GET /projects/activity` 只返回任务动态的固定字段，不暴露审计元数据。

## 偏差

- **迁移 0053 删除了 `review_pending` 列**（方案写“迁移只能加列 / 表 / 约束”）。原因：这一列是同一批工作里 T-0163 刚加的，
  它的信息已无损转成 `completion_seq = 1` + 没有查看记录；保留一个没人写的列会让“待查看”有两个来源。
  downgrade 按“已完成、轮次 > 0、没有任何人看过这一轮”回填 `review_pending`。没有删除或改写任何任务。
- 日历上任务截止只占一格（不画成时间段）；邮件页没有做方案图里的“提醒”分类筛选（Gmail 没有对应的只读字段）。见各任务日志。

## 影响

- 首页“工作概况 / 任务进展”的键改为 `todo / running / awaiting_view / blocked`，阻塞行显示原因，“处理阻塞”打开任务详情。
- 智能体工具没有变化：`complete_task` 完成后所有人进入待查看（`self_viewed=False`），智能体不能替人标已查看，也没有标阻塞的工具。
- 浏览器验收脚本（`home_browser.cjs`、`sprint6_browser.cjs`）改为“标为已查看”。
