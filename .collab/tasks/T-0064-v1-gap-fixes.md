---
id: T-0064
title: V1 补漏：成员管理、日历的 Vikunja 截止日期派生日程、ProjectProgress 与 IM 快捷入口组件、过时文案与文档矛盾、规范路由更新（设计文档核查）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs/api-contract.md,docs/DEVELOPMENT_SPEC.md,docs/release-checklist.md,docs/versions.md,docs/data-dictionary.md,docs/decisions,tests/e2e
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0064 V1 补漏：成员管理、日历的 Vikunja 截止日期派生日程、ProjectProgress 与 IM 快捷入口组件、过时文案与文档矛盾、规范路由更新（设计文档核查）

## 目标
设计文档核查（2026-11-01）发现的 V1 缺口与文档问题，人类在对话中同意补上，并同意按实现更新规范第 8 节的 V2 路由（`docs/DEVELOPMENT_SPEC.md` 是人类负责的文件）。

## 验收标准
- [x] 规范 §9.2：成员管理——列出成员、添加成员（已有账号按邮箱加入，或创建账号并设初始密码）、改角色、移除；只有 owner 能授予或收回 admin，admin 管理 member / viewer，owner 不能被移除或降级；全部写审计日志；前端设置页
- [x] 规范 §1.2：日历显示 Vikunja 任务截止日期的只读派生日程
- [x] 规范 §8.2、方案第 2 节：首页组件 ProjectProgress（项目进度）与 IM 快捷入口（原 AIQuickAsk，改为打开 IM）
- [x] 界面过时文案（“Sprint 3 · 任务与集成”“将在 Sprint 5 开放”）；`docs/release-checklist.md`（密钥轮换）、`docs/versions.md`（“尚未进入正式部署”）与实现一致
- [x] 规范第 8 节的 V2 路由按实现更新（人类同意）；IM 消息保留期 365 天的决定写进文档
- [x] 后端 ruff / mypy / pytest、前端 lint / typecheck / test / build / check:api、相关浏览器验收通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 18:03 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-01 18:03 UTC · human · owner unassigned → claude：人类在对话中指派：按设计文档核查结果补 V1 缺口；同意按实现更新规范第 8 节的路由
- 2026-11-01 18:03 UTC · claude · 开始任务
- 2026-11-01 19:09 UTC · claude · 全部实现：成员管理（GET/POST/PATCH/DELETE /workspaces/{id}/members，设置页成员面板，Sprint 1 浏览器验收加成员步骤）；日历显示 Vikunja 任务截止（前端用 /tasks 的 due_after/due_before，只读，链接到任务页搜索）；首页组件 project_progress（新接口 GET /projects/progress）与 im_quick_chat（发一句话并打开 IM 会话）；去掉过时文案；release-checklist 密钥轮换、versions.md 1.5 标题；规范 §8 V2 路由按实现更新（人类同意）；365 天保留期写进 deployment.md 第 17.3 节与 OC-3 ADR。越界说明：docs/deployment.md 不在本任务 paths 内（属于已完成的 T-0063，都是 claude 的任务），只加了 IM_MESSAGE_RETENTION_DAYS 一行
- 2026-11-01 19:09 UTC · claude · status in_progress → done：V1 缺口已补齐，检查见 PR
