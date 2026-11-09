---
id: T-0177
title: 二批四页（六）：运维健康视图（服务分组计数、异常详情、按单位格式化的指标图、存储状态、显式存储自检）
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/operations,apps/web/src/pages/OperationsPage.vue,apps/web/src/app/router.ts,apps/web/tests,tests/e2e
depends_on:
created: 2026-11-09
updated: 2026-11-09
---

# T-0177 二批四页（六）：运维健康视图（服务分组计数、异常详情、按单位格式化的指标图、存储状态、显式存储自检）

## 目标
运维页改成健康视图：健康摘要（正常 / 需要关注 / 未知 / 未配置）、异常优先的服务表与详情、逐序列独立的指标图、存储状态；指标格式可解释。
方案见 `docs/v2/pages-redesign-2/`（Task 6）。

## 验收标准
- [ ] 服务分四组计数，停用的模块不算故障；列表异常优先、名称稳定排序，不写死服务数量
- [ ] `formatMetricValue`：null→“—”、1.2 req/s、0.184 秒→184 ms、0.012→1.2%、count 原样；null 不补 0，P95 不跨序列平均
- [ ] 折线遇 null 断开并有数据表替代；切换范围后旧响应不覆盖新图；Prometheus 错误区分 stale 与 unavailable
- [ ] 存储读写自检只在有权用户显式点击时调用一次；进页、刷新、切范围、定时刷新都不调用；无副本显示“未配置副本”
- [ ] 没有 last_success 字段就不显示“上次成功”；grafana_url 不合法时无跳转；viewer 看不到受限详情，403 后清理
- [ ] `operations-overview.test.ts` 与既有运维测试通过；亮 / 暗、桌面 / 手机截图

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 04:04 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 04:08 UTC · claude · 开始任务
