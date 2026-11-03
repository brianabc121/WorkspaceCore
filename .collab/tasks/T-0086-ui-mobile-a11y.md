---
id: T-0086
title: UI 优化第 4 批：手机底部导航、触控尺寸、无障碍复查
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: apps/web,tests/e2e,docs
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0086 UI 优化第 4 批：手机底部导航、触控尺寸、无障碍复查

## 目标
人类要求（2026-11-02 / 11-03）：「给我重新优化一下整个工作台的 UI 以及功能」「继续做第 4 批」。第 4 批：手机与无障碍。
手机上常用页面要点开侧栏才能切换；部分按钮（视图切换、勾选框）在手机上偏小；前 3 批改动后需要一次整体的无障碍复查。

## 验收标准
- [x] 手机（≤760px）底部导航：总览、任务、项目、日历，第五项 owner / admin 是消息（带未读数）、其他角色是知识库；标出当前页；适配底部安全区；桌面不显示
- [x] 手机上的触控尺寸：顶栏图标、视图切换、筛选标签、勾选框、日历日期等至少 40px 左右的点按区域
- [x] 无障碍复查：用 axe-core 扫描主要页面（亮 / 暗 / 手机），修掉发现的问题；键盘可达、焦点可见、减少动效偏好
- [x] 前端 lint / typecheck / test / build / check:api 通过；浏览器截图验收；受影响的 e2e 脚本同步更新

## 背景 / 约束 / 参考
- 只改前端，接口不变；axe-core 只作为本地检查工具，不加入项目依赖。
- 依赖 T-0083 ~ T-0085。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 00:27 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 00:27 UTC · human · owner unassigned → claude：人类在对话中指派：继续做第 4 批（手机底部导航、触控尺寸、无障碍复查）
- 2026-11-03 00:27 UTC · claude · 开始任务
- 2026-11-03 00:43 UTC · claude · 完成：手机底部导航（BottomNav.vue，5 项，消息带未读数，viewer/member 第五项为知识库，安全区适配，侧栏停在其上方）；手机触控尺寸；axe-core 4.11.4（仅本地）扫描 17 个页面 + 登录页（亮 / 暗 / 390px），修掉月视图的 role=grid、对比度 2.88、6px 可点圆点 3 个问题后 0 个问题；键盘 Tab 检查焦点全部可见（快速添加框加焦点环）；减少动效全局生效。新增 tests/bottom-nav.test.ts；ADR 追加第 6 节。
- 2026-11-03 00:43 UTC · claude · status in_progress → done：UI 第 4 批（手机与无障碍）完成：web lint / typecheck / test 362 / build / check:api 通过；axe 0 个问题；键盘焦点检查通过；前 3 批的浏览器检查重跑通过
