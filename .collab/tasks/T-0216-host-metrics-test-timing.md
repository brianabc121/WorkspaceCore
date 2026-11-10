---
id: T-0216
title: 主机指标测试的计时：模拟时间以“现在”为基准（test_host_metrics_api 偶发失败）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api/tests/test_host_metrics_api.py
depends_on:
created: 2026-11-10
updated: 2026-11-10
---

# T-0216 主机指标测试的计时：模拟时间以“现在”为基准（test_host_metrics_api 偶发失败）

## 目标
main 上 8f0b16a（PR #187 合并）的 ci-api 失败：`tests/test_host_metrics_api.py::test_report_computes_rates_and_trends`
断言 `990 <= rx_rate`，实际 977。T-0188 时也偶发过（当时只说明并重跑）。

原因：服务端按两次上报之间的真实时间算速率；测试的 `shift()` 把上一次上报时间“在原值上减 60 秒”，
所以间隔 = 60 秒 + 测试在两次上报之间花的时间（查询状态、改库、清限频）。CI 慢时多出约 1.4 秒，
60000 字节 / 61.4 秒 = 977，超出 1% 的容差；`cpu_cores`（±2%）在同样条件下也会失败。

## 验收标准
- [x] 复现：在两次上报之间加 1.5 秒延迟，原写法失败（rx_rate 974）
- [x] `shift()` 改为把上一次上报时间设为 `now() - seconds`；同样加 1.5 秒延迟时通过
- [x] 去掉延迟后整个文件连跑 3 次都是 10 passed；ruff / format / mypy 通过
- [x] 不放宽断言的容差，不改应用代码
- [x] PR 上 CI 全部通过：按 D-0004，最新提交上所有检查成功才会合并，合并即满足

## 背景 / 约束 / 参考
- 失败的运行：https://github.com/brianabc121/WorkspaceCore/actions/runs/42898025066（api job 130116232189）
- 速率计算：`app/domain/host_metrics.py` 中 `seconds = (now - state.reported_at)`

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 21:33 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 21:33 UTC · claude · 开始任务
- 2026-11-10 21:33 UTC · claude · status in_progress → done：shift() 以 now() 为基准；本地复现（加 1.5 秒延迟原写法失败、新写法通过），整个文件连跑 3 次 10 passed；合并以 PR 上 CI 全部通过为前提
