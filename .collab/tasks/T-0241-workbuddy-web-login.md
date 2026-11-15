---
id: T-0241
title: 大模型客户端：WorkBuddy（CodeBuddy）改为网页登录，不再用容器里的客户端登录
owner: claude
status: done
priority: P2
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/tests,apps/web/src/services/schema.d.ts,docs
depends_on: T-0240
created: 2026-11-15
updated: 2026-11-15
---

# T-0241 大模型客户端：WorkBuddy（CodeBuddy）改为网页登录，不再用容器里的客户端登录

## 目标
人类 2026-11-15 要求：WorkBuddy 改为用网页的形式登录，不再使用客户端（进容器运行官方程序登录）。

做法：锁定版本的 CodeBuddy Code 2.184.0 有本机服务模式（`codebuddy --serve`），提供发起登录（返回要人打开的网址）、查询登录状态、退出登录三个接口。管理器在登录期间启动它，把网址给页面，等人登录完。接口的形状在本会话里实测过，见 `services/llm-bridge/bridge/drivers/web_login.py` 开头。

## 验收标准
- [x] 管理器：WorkBuddy 的登录方式变成 网页登录（国内站 / 国际站）+ API 密钥；不再有“容器里已有的登录”
- [x] 登录服务只在登录期间开着：只监听容器里的 127.0.0.1、访问口令每次随机、完成 / 失败 / 10 分钟超时后关掉
- [x] 登录成功后显示官方程序报告的账号名；退出登录时让官方程序也退出
- [x] 页面：默认网页登录，显示可点击的登录网址，登录完自动更新；拿不到网址时写明原因
- [x] 管理器（仿真的本机服务）与前端的测试

## 背景 / 约束 / 参考
- 没有 CodeBuddy 账号，也访问不了它的站点：**登录成功之后的那一段（网址打开后的页面、登录完成后状态变成已登录、之后的回合能用这份登录）没有实测**，按接口的代码和实测到的应答形状实现。
- 以前用“容器里已有的登录”登录过的 WorkBuddy 账号继续可用，只是不能再用这种方式重新登录。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 10:40 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 10:40 UTC · human · owner unassigned → claude：人类在对话中指派（2026-11-15）：WorkBuddy 改为网页登录，不再使用客户端
- 2026-11-15 10:40 UTC · claude · 开始任务
- 2026-11-15 10:40 UTC · claude · status in_progress → done：实现与测试完成，随本 PR 合并；真实账号下的登录由人类验收
