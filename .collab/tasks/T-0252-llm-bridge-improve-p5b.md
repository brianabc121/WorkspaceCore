---
id: T-0252
title: 大模型客户端改进第 5 期下：回合详情、改动补丁、审计、通知、保留期与导出（V2）
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/app/domain/audit_labels.py,services/api/migrations,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/features/audit/display.ts,apps/web/src/services/schema.d.ts,apps/web/tests,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0252 大模型客户端改进第 5 期下：回合详情、改动补丁、审计、通知、保留期与导出（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 4.2 的 B2、B3、B6、B7、B8。

## 验收标准
- [x] B2 过程里显示命令的输出和非零的退出码
- [x] B3 开发回合留下改动补丁，归档后还在
- [x] B6 智能体的写操作、下载附件、导出进审计；事件有中文名；审计页能点到会话
- [x] B7 智能体的回合失败、平台因额度暂停时通知管理员；有迁移
- [x] B8 保留期设置（默认一直保留）和按期清理；会话导出成 Markdown
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 21:24 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 21:24 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 5 期下）
- 2026-11-15 21:24 UTC · claude · 开始任务
- 2026-11-15 21:24 UTC · claude · 迁移 0074 给 llm_bridge_bridges 加 pause_notified。保留期清理是不可恢复的删除：默认 0（一直保留），只有管理员在设置里填了天数才会清。越界：app/domain/audit_labels.py 加了 llm_bridge.* 的中文名，apps/web/src/features/audit/display.ts 加了到会话的链接。
- 2026-11-15 21:24 UTC · claude · status in_progress → done：第 5 期下完成
