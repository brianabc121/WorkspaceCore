---
id: T-0066
title: 规范 §2 / §8：引入 shadcn-vue，右侧可收起的 AI / 详情抽屉，Task Drawer（人类要求）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web,docs/decisions,docs/architecture.md,docs/versions.md,README.md,tests/e2e
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0066 规范 §2 / §8：引入 shadcn-vue，右侧可收起的 AI / 详情抽屉，Task Drawer（人类要求）

## 目标
规范第 2 节技术栈写的是“Tailwind CSS + shadcn-vue”，目前只有 Tailwind；第 8.1 节要求桌面布局右侧有 320 ~ 420px 可收起的 AI / 详情抽屉，
第 8.4 节要求任务的新建 / 编辑用 Task Drawer（目前是弹窗）。人类在对话中要求实现（设计文档核查时留下的待定项）。

## 验收标准
- [x] 引入 shadcn-vue：组件源码来自已核验的发布标签，依赖用精确版本并写 ADR；主题令牌映射到现有变量，亮暗主题都能用
- [x] 右侧抽屉：顶栏开关；宽屏停靠 320 ~ 420px、窄屏侧滑面板；“AI 助手”页经工作台 IM 接口与 OpenClaw 实例对话（owner / admin），member 看到说明
- [x] Task Drawer：任务新建 / 编辑在抽屉的“详情”页，原有行为（幂等键、版本冲突、结果未知、提交中不能关闭）不变
- [x] 前端 lint / typecheck / test / build；真实浏览器验收（宽屏、390px、真实 OpenIM）

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 20:23 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-01 20:23 UTC · human · owner unassigned → claude：人类在对话中指派：实现规范第 8 节的右侧 AI / 详情抽屉与 Task Drawer，并按第 2 节引入 shadcn-vue
- 2026-11-01 20:23 UTC · claude · 开始任务
- 2026-11-01 21:01 UTC · claude · 完成：shadcn-vue（v2.9.0 标签的 sheet / tabs / button，去掉 @vueuse/core 与 @lucide/vue 依赖）、components.json、@ 别名、主题令牌映射；右侧抽屉（宽屏停靠 380px、窄屏 Sheet；AI 助手经 IM 与实例对话；详情页 + DrawerDetail Teleport）；任务新建 / 编辑改用抽屉。sprint6_browser.cjs 加抽屉两步，并修了导出前要等序号的时序问题（T-0063 留下的）。ADR：docs/decisions/2026-11-01-shadcn-vue-and-drawer.md
- 2026-11-01 21:01 UTC · claude · status in_progress → done：抽屉与 shadcn-vue 完成，检查见 PR
