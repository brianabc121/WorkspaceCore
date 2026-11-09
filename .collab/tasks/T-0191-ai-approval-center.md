---
id: T-0191
title: AI 审批与管理区（A2）：审批列表、固定参数详情与执行结果
owner: claude
status: todo
priority: P1
branch:
paths: apps/web/src/pages/AiPage.vue,apps/web/src/features/ai,apps/web/tests
depends_on: T-0189, T-0190
created: 2026-11-09
updated: 2026-11-09
---

# T-0191 AI 审批与管理区（A2）：审批列表、固定参数详情与执行结果

## 目标
AI 审批页按效果图 01 改成待处理表 + 详情：汇总卡、三个视图、服务端筛选与到期排序、固定参数、决定与真实执行结果。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 A2）

## 验收标准
- [ ] 汇总卡与列表用同一用户 / 空间 / 非状态条件；倒计时按 summary.as_of 校时，归零禁用并重新读取
- [ ] critical 批准需本人密码（1–128，尝试后清空），拒绝不需要；If-Match 用当前 version；409 / 已处理 / 失权先刷新
- [ ] 批准后显示 executing / succeeded / failed / unknown 真实结果；网络未知先 GET，不盲目重试
- [ ] 来源推断标“来源推断”，无理由 / 前后值时不编造；未知工具安全 JSON；外部文字只当文本
- [ ] `/ai?id=`、`/approvals?id=` 与 IM 卡片深链照常；新测试与原 ai 测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
