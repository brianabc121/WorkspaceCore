---
id: T-0211
title: 应用中心前端：模块列表与详情、四类状态、自定义页面入口、操作记录、接入指南（方案包 AC-3/4/5）
owner: claude
status: done
priority: P1
branch: b6-model-connections
paths: apps/web,docs
depends_on: T-0210
created: 2026-11-10
updated: 2026-11-10
---

# T-0211 应用中心前端：模块列表与详情、四类状态、自定义页面入口、操作记录、接入指南（方案包 AC-3/4/5）

## 目标
应用中心前端（方案包 AC-3/4/5，效果图 `docs/v2/app-center/assets/01-app-center.png`）：/settings/modules 按效果图改版——
汇总卡、搜索 / 状态筛选、模块卡片（声明能力、外部服务、待部署提示）、右侧详情（概览 / 功能能力 / 操作记录，启用 / 部署 / 连通 / 同步四类状态分开），
自定义页面入口（复用 PageForm 与同一份导航布局，带版本整份保存，409 保留草稿），操作记录，接入指南。模块的主页面与设置页入口由模块自己的 management 声明，宿主不写死 news。

## 验收标准
- [x] ModuleDefinition.management（primaryPath / settingsPath / category / helpPath），新闻早报声明 /news 与 /settings/news；一致性测试
- [x] 应用中心列表、汇总、搜索 / 筛选、模块卡片与详情；启用、部署、健康、同步四类状态独立显示；admin 只读、owner 启停与重试
- [x] 启停带 expected_state_version；网络结果未知时先重新读取，不重发；切换用户 / 工作空间不串数据
- [x] 自定义页面：列表、添加 / 编辑 / 移除入口，单次整份带 version 保存，409 保留草稿并让 owner 确认后再合并保存；移除入口不删除目标
- [x] 操作记录（全部模块 / 单个模块）、接入指南（不提供安装、升级、清除数据）
- [x] vitest 覆盖上述行为；lint / typecheck / test / build / check:api 通过；手机 390、桌面 1440、暗色、键盘可用

## 背景 / 约束 / 参考
方案包：`docs/v2/app-center/`（设计、接口与一致性契约、前后端计划、验收矩阵、效果图）。方案包里的 AC-0 ~ AC-6 是局部编号：AC-0/1/2 → T-0210，AC-3/4/5 → T-0211，AC-6 → T-0212。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-10 13:52 UTC · claude · 创建任务（owner: claude）
- 2026-11-10 13:52 UTC · claude · 开始任务
- 2026-11-10 14:58 UTC · claude · status in_progress → done：前端完成：应用中心按效果图改版（汇总、卡片、详情四类状态、自定义页面带版本保存、操作记录、接入指南）；vitest 757 个、lint / typecheck / build / check:api 通过，真实浏览器验收通过
