---
id: T-0156
title: 首页聚合并发测试：放宽执行时间余量（main 上 ci-api 偶发失败）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/tests/test_dashboard_api.py
depends_on:
created: 2026-11-07
updated: 2026-11-07
---

# T-0156 首页聚合并发测试：放宽执行时间余量（main 上 ci-api 偶发失败）

## 目标
main 上 9329522（PR brianabc121/WorkspaceCore#173 合并）的 ci-api 在
`tests/test_dashboard_api.py::test_concurrent_overviews_queue_instead_of_timing_out` 偶发失败：6 个并发请求里
最后一个的 tasks 模块是 `unavailable`（超时）。测试里的假模块执行 0.2 秒、超时 0.5 秒，只留 0.3 秒余量，
CI 机器忙时事件循环一慢就超时；同一提交在 PR 上和本机两次全量测试都通过。排队时间本来就不计入超时
（`overview_gate` 之内才开始计时），所以不是聚合本身的问题。

## 验收标准
- [x] 假模块执行 0.15 秒、超时 1 秒、16 个并发（数据库连接池 20 以内）：最后的请求排队加执行约 1.2 秒，
      仍然证明“排队时间不计入超时”；执行本身离超时有 0.85 秒余量
- [x] 本机连续多次运行通过；ruff / mypy 通过

## 背景 / 约束 / 参考
前一次同类修复：T-0150（浏览器验收的偶发失败）。不跳过、不放宽断言，只调整假模块的时间参数。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-07 19:15 UTC · claude · 创建任务（owner: claude）
- 2026-11-07 19:16 UTC · claude · 开始任务
- 2026-11-07 19:17 UTC · claude · status in_progress → done：假模块执行 0.15 秒、超时 1 秒、16 个并发：仍证明排队不计入超时，执行余量从 0.3 秒放宽到 0.85 秒；本机连续 5 次通过
