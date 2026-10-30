---
id: T-0024
title: 修复新增组件的布局行号上界（S2-04 审查遗留）
owner: codex
status: done
priority: P2
branch: codex/T-0024-widget-row-boundary
paths: apps/web/src/features/dashboard/WidgetEditor.vue,apps/web/tests/widget-editor.test.ts
depends_on:
created: 2026-10-30
updated: 2026-10-30
---

# T-0024 修复新增组件的布局行号上界（S2-04 审查遗留）

## 目标
修复 PR #23 的已确认非阻塞意见：现有组件的 y + h 超过 999 时，新增组件的默认桌面/移动端行号超出契约范围，导致合法配置无法保存。T-0022 依赖的 T-0019 尚未合并，因此独立交付这个不依赖 Sprint 3 接口的审查修复，供 T-0022 后续接续。

## 验收标准
- [x] 新增组件时，默认桌面与移动端 y 保持不大于 999；范围内的 bottom 原样保留
- [x] 配置已有组件时保留原布局，不因 bottom 越界而改变
- [x] 仍拒绝无效组件配置；回归测试先失败后通过
- [x] Node 22 + npm ci；lint / typecheck / test / build / check:api 通过
- [x] collab check 通过，开 PR 由 Claude 审查，不合并

## 背景 / 约束 / 参考
- PR #23 行内意见：https://github.com/brianabc121/WorkspaceCore/pull/23#discussion_r4421529353
- Sprint 2 契约 ADR 第 5 节：y 范围 0–999，服务端不检查重叠；本修复沿用该约定，不改变接口或网格排布规则
- 不修改 T-0022 的状态与验收清单，不提前实现尚未确定的 Sprint 3 接口
- 短计划：真实 WidgetEditor 提交回归 → 限制新组件默认行号 → Node22 完整验证 → 任务收工/PR

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-30 14:18 UTC · codex · 创建任务（owner: codex）
- 2026-10-30 14:18 UTC · codex · 开始任务
- 2026-10-30 14:19 UTC · codex · 已用真实 WidgetEditor 回归复现：bottom=1002/1023 时保存事件缺失，新增today_tasks同样失败；8项中3失败5通过。根因是默认desktop/mobile的y直接使用已有组件下边缘，超过契约999上限。仅把新建默认行号限制为min(bottom,999)，已有布局仍优先使用，配置校验保留；修复后8项全通过。T0019仍todo，T0022暂不开始。
- 2026-10-30 14:20 UTC · codex · status in_progress → done：默认新增组件的desktop/mobile行号限制在999以内，范围内保留；已有组件布局与配置校验不变。先复现3失败5通过，修复后8项全通过；Node22 npm ci与lint/typecheck/test（90/90）/build/check:api通过，独立审查无可操作问题。T0019未合并，不提前开始T0022。开PR由Claude审查，不合并。
