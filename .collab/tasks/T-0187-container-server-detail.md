---
id: T-0187
title: 容器服务器（四）：容器详情、执行记录与生命周期动作
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/src/features/runner,apps/web/tests
depends_on: T-0186
created: 2026-11-09
updated: 2026-11-09
---

# T-0187 容器服务器（四）：容器详情、执行记录与生命周期动作

## 目标
选中容器在右侧（手机为抽屉）看资源负载、执行记录、配置与回收，并保留启动、停止、归档、删除、撤销删除、恢复、保护等原有动作。方案见 `docs/v2/container-server-redesign/`（产品规范、指标与接口约定、五阶段计划、效果图）；人类在对话中要求“根据效果图和设计文档全部落实”。（计划 Task 4）

## 验收标准
- [x] `environment_id` 写在地址里，筛选之外也按新详情接口定位；404 / 失权 / 换工作空间清掉旧详情；关闭后焦点回到原行
- [x] 执行记录按 environment_id 读取，多条并行如实列出；queued / timeout / unknown 不冒充容器状态；用途、命令、输出只当文本显示
- [x] 动作沿用 POST actions + version；归档 / 删除的数据影响按类别提示；409 先刷新，WRITE_UNKNOWN 先查状态，不重复发送；打开或刷新详情不发 POST
- [x] `container-server-detail.test.ts` 与 runner 旧测试通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-09 09:33 UTC · claude · 创建任务（owner: claude）
- 2026-11-09 10:14 UTC · claude · 开始任务
- 2026-11-09 10:14 UTC · claude · 详情（宽屏 ≥1440 右侧一栏、其余右侧抽屉）：资源负载、当前执行、执行记录、配置与回收、单一保护控件、生命周期动作（version、按类别的确认、已发出提示、409 / 结果未知不重发）；container-server-detail 7 个测试
- 2026-11-09 10:14 UTC · claude · status in_progress → done：容器详情与生命周期动作完成
