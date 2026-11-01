---
id: T-0067
title: 顶栏全局创建菜单（规范 §8.1；设计文档核查补漏）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web,docs/decisions,README.md,tests/e2e
depends_on:
created: 2026-11-01
updated: 2026-11-01
---

# T-0067 顶栏全局创建菜单（规范 §8.1；设计文档核查补漏）

## 目标
规范 §8.1 要求顶栏有“创建”入口；目前顶栏只有一个禁用的“创建 · 即将开放”占位。改成可用的全局创建菜单：
在任何页面都能直接开始新建任务、日程、文档收藏等，不必先找到对应页面再找“新建”按钮。

## 验收标准
- [x] 顶栏“创建”按钮打开下拉菜单（shadcn-vue DropdownMenu，来源与改动记入 ADR），支持键盘操作
- [x] 条目按角色列出：member 及以上有新建任务 / 日程 / 收藏文档；owner、admin 另有新建群聊、添加成员；owner 另有登记 OpenClaw 实例；viewer 不显示按钮
- [x] 选中后打开对应页面已有的新建表单（不复制表单逻辑），新建任务进 Task Drawer；地址里的参数用后去掉，在当前页再次选择也能打开
- [x] 页面暂时不能新建时不报错、不弹空表单，由页面显示原因；没有知识来源时管理员先进入来源配置
- [x] 表单打开时焦点进入表单，关闭后回到“创建”按钮
- [x] 单元测试覆盖角色、各条目、参数清理与焦点；真实浏览器验收（宽屏、键盘、390px 窄屏）
- [x] web lint / typecheck / test / build / check:api 通过；ADR、README 更新

## 背景 / 约束 / 参考
- 规范 §8.1；ADR `docs/decisions/2026-11-01-shadcn-vue-and-drawer.md` 第 5 节
- 只改前端；没有接口、数据库变化

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-01 21:24 UTC · claude · 创建任务（owner: claude）
- 2026-11-01 21:24 UTC · claude · 开始任务
- 2026-11-01 21:24 UTC · claude · 人类在对话中指派：“把创建按钮也做了”——顶栏“创建 · 即将开放”占位改成可用的全局创建菜单
- 2026-11-01 21:40 UTC · claude · 顶栏“创建”下拉菜单（shadcn-vue DropdownMenu）+ useCreateIntent：跳到模块页带 ?create=1 打开已有新建表单，用后去掉参数；按角色列条目；焦点由菜单接管。web lint/typecheck/test(33 文件 328 个)/build 通过；本机真实浏览器验收（宽屏、键盘、390px）通过；sprint6_browser 的建群改为从创建菜单进入
- 2026-11-01 21:42 UTC · claude · status in_progress → done：顶栏全局创建菜单完成：按角色列条目，复用各页新建表单；单元测试与真实浏览器验收通过
