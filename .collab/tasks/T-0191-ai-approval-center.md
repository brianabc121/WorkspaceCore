---
id: T-0191
title: AI 审批与管理区（A2）：审批列表、固定参数详情与执行结果
owner: claude
status: done
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
- [x] 汇总卡与列表用同一用户 / 空间 / 非状态条件；倒计时按 summary.as_of 校时，归零禁用并重新读取
- [x] critical 批准需本人密码（1–128，尝试后清空），拒绝不需要；If-Match 用当前 version；409 / 已处理 / 失权先刷新
- [x] 批准后显示 executing / succeeded / failed / unknown 真实结果；网络未知先 GET，不盲目重试
- [x] 来源推断标“来源推断”，无理由 / 前后值时不编造；未知工具安全 JSON；外部文字只当文本
- [x] `/ai?id=`、`/approvals?id=` 与 IM 卡片深链照常；新测试与原 ai 测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 13:53 UTC · claude · A2 前端完成（分支 b5-a2）：AI 审批按效果图 01 改为四张汇总卡（来自 /summary，标明范围）、三个视图（?view= 待审批 / activity / exceptions）、服务端筛选与到期排序（换条件从第一页读、旧请求取消）、待处理表 + 执行异常区、?id= 详情（宽屏右栏、窄屏抽屉；列表外记录按 ID 读）。详情：固定参数（已知工具可读字段 + 折叠原始 JSON，未知工具安全 JSON，摘要不当校验）、来源推断标注、被删实例保留历史名、审批流程按真实状态、倒计时按 as_of 校时归零禁用并重读；critical 密码 1–128 只写、每次尝试 / 换记录清空，拒绝不要密码；409 / 已处理 / 失权刷新；响应丢失先 GET 同一记录，executing / unknown 不给重试；批准后如实显示执行中 / 成功 / 失败 / 结果未知。新测试 tests/approval-center-redesign.test.ts 18 条（先红后绿），ai.test.ts 按新结构改选择器；lint / typecheck / 全量 vitest 668 通过 / build / check:api 通过；截图 1440/1280/768/390 亮暗无横向滚动。
- 2026-11-09 13:54 UTC · claude · status todo → done：AI 审批页改版完成：四张汇总卡与列表同一组服务端条件、倒计时按 as_of 校时、critical 密码只写、批准与执行结果分开、网络未知先 GET；新测试 18 例，合并后全量通过
