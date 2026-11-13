---
id: T-0229
title: 大模型会话桥（三）：同机部署与设置页的浏览器验收（真实后端 + 真实前端 + 真实会话桥容器），顺带修页面小问题
owner: claude
status: in_progress
priority: P1
branch: claude/trusting-euler-480tpv
paths: tests/e2e,.github/workflows/ci-llm-bridge.yml,apps/web/src/modules/llm_bridge,apps/web/tests,services/api/app/modules/llm_bridge,services/api/tests,infra/llm-bridge,docs
depends_on:
created: 2026-11-13
updated: 2026-11-13
---

# T-0229 大模型会话桥（三）：同机部署与设置页的浏览器验收（真实后端 + 真实前端 + 真实会话桥容器），顺带修页面小问题

## 目标
PR #198 合并时设置页只有 jsdom 测试，没有做 CLAUDE.md 要求的浏览器验收；同机部署那条路（`make llm-bridge-up`、
加入工作台的内部网络）也从没真正跑过。本任务补上：在真实后端 + 真实前端 + 真实会话桥上验收设置页，把同机部署接进 CI，
并修掉验收中发现的问题。

## 验收标准
- [x] `tests/e2e/llm_bridge_browser.cjs`：13 步（启用模块、登记、账号槽位与通道、API 密钥登录与退出、Claude 风险确认、设置、开发环境、会话、实例权限、令牌不对的提示、admin 只读与 member 不可见、手机宽度、停用与删除）
- [x] 在本机对真实运行的工作台 API、前端、会话桥（真实的 codex / codebuddy 程序）跑通，并截图检查桌面与手机宽度
- [x] 修掉发现的问题：`make llm-bridge-up` 读不到 `.env` 里的令牌；令牌改由部署命令生成、`make llm-bridge-token` 查看；密钥登录不再拿密钥指纹当账号；退出登录后不再显示登录方式；通道表“用法”列折行；关闭实例权限时保留已选平台；删除环境的确认文字；“结果未知”没有中文
- [ ] CI：`ci-llm-bridge` 的 `workspace` job（`make up` + `make llm-bridge-up` + 冒烟 + 浏览器验收）通过
- [x] 文档：部署指南第 25 节、接口文档、方案第 18 节、核验记录

## 背景 / 约束 / 参考
- 需要真实账号的验收（各平台的登录流程、真实回合、Codex 沙箱在容器里的表现）不在本任务范围，仍然需要人类登录。
- 本会话里 claude 程序在什么凭据都不给的情况下也报告已登录（机器在环境变量之外提供了登录）：本机验收时会话桥不接 Claude 程序。
- 本会话没有可用的 Docker 构建（Debian 源被拦），同机部署只能在 CI 上实跑。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 14:43 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 14:43 UTC · claude · 开始任务
- 2026-11-13 15:05 UTC · claude · 本机对真实运行的工作台 API、前端（Vite）、会话桥（真实的 codex / codebuddy 程序，不接 Claude 程序）跑通浏览器验收 13 步，并截图检查了桌面与手机宽度。修了：make llm-bridge-up 读不到 .env 里的令牌（少了 --env-file）、令牌改由部署命令生成并用 make llm-bridge-token 查看、密钥登录不再拿密钥指纹当账号（新增 registered 字段）、几处页面小问题。ci-llm-bridge 新增 workspace job（同机部署 + 冒烟 + 浏览器验收）。本地检查：会话桥 23 passed，后端相关 63 passed，前端 795 passed。下一步：看 CI 的 workspace job，同机部署在本会话里没法实跑（没有可用的 Docker 构建）。
