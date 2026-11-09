---
id: T-0198
title: AI 审批与管理区（C2）：审计事件表、详情与导出
owner: claude
status: done
priority: P1
branch:
paths: apps/web/src/pages/AuditPage.vue,apps/web/src/features/audit,apps/web/tests
depends_on: T-0189, T-0197
created: 2026-11-09
updated: 2026-11-09
---

# T-0198 AI 审批与管理区（C2）：审计事件表、详情与导出

## 目标
审计日志页按效果图 08：筛选条、事件表、详情、导出当前筛选。方案见 `docs/v2/ai-admin-redesign/`（八页规范、接口约定、三个子计划、8 张效果图）；人类在对话中要求“根据效果图和开发文档进行全面落实”。（计划 C2）

## 验收标准
- [x] 筛选在服务端；只显示“已加载 N 条”，不编造总数；详情 `?id=` 可直链，不在当前页也能读
- [x] 导出用当前服务端筛选；超限 / 无权给出提示；ObjectURL 用完就释放；不说“已下载完成”
- [x] 当前名称标“当前名称”；未知 action 原样；metadata 只当文本
- [x] 新测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 12:51 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 14:04 UTC · claude · 前端 C2 已在分支 b5-c2 提交 0cff913：features/audit（筛选条、事件表、详情、导出）+ AuditPage 改用 ManagementPage；新测试 tests/audit-center-redesign.test.ts 12 项通过，全量 vitest 70 文件 672 项通过，lint / typecheck / build / check:api 通过；1440/1280/768/390 亮暗截图无横向滚动。待办：router.ts 给 /settings/audit 加 wide（不在本任务 paths），密钥库页需支持 ?secret= 定位。
- 2026-11-09 14:08 UTC · claude · status todo → done：审计日志页改版完成：服务端筛选写在地址里、只显示已加载条数、?id= 按 ID 读详情、导出当前服务端筛选（全部时间先提示缩小、超限 / 无权分别提示）；新测试 12 例，合并后前端全量 74 文件 725 测试
