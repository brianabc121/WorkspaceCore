---
id: T-0080
title: 工作台保存并向管理员显示 Vikunja、n8n 的登录账号密码（加密保存、点击显示并审计）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api/app/models,services/api/app/repositories/app_logins.py,services/api/app/domain/app_logins.py,services/api/app/api/v1/app_logins.py,services/api/app/api/v1/__init__.py,services/api/app/api/deps.py,services/api/app/schemas/app_logins.py,services/api/migrations/versions,services/api/app/commands/connect.py,services/api/openapi.json,services/api/tests,apps/web/src,apps/web/tests,scripts/deploy.sh,docs
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0080 工作台保存并向管理员显示 Vikunja、n8n 的登录账号密码（加密保存、点击显示并审计）

## 目标
人类要求在工作台的「项目」页（Vikunja）与「自动化」页（n8n）直接看到这两个管理界面的登录账号密码，
自己复制粘贴去登录。之前 `deploy.sh connect` 生成的密码只在终端显示一次，工作台不保存。

## 验收标准
- [x] 新表 `workspace.app_logins`（迁移 0019，可回退）：每个工作空间每个应用一条，密码只以 `enc:v1:` 引用保存，加入密钥轮换
- [x] 接口只给 admin 以上：列表不含密码；`POST …/{app}/reveal` 取明文，带 CSRF、写审计 `app_login.revealed`、`Cache-Control: no-store`；
      可新建 / 修改（乐观并发）/ 删除；member、viewer 403，非成员 404；审计与日志里没有密码
- [x] `connect` 新建或验证过的 Vikunja 用户、n8n owner 自动保存到工作台（`connect save-login`，密码只经标准输入）
- [x] 前端：两个页面各一张卡片（只有 owner/admin 看得到），默认遮住，点击显示、复制用户名 / 密码、打开登录页、填写与修改
- [x] 测试：API（权限、加密、审计、no-store、并发）、契约、密钥轮换、connect 命令、前端单元测试与浏览器 mock；
      `apps/web` lint / typecheck / test / build / check:api 通过
- [x] ADR `docs/decisions/2026-11-02-app-logins.md`，更新 deploy-connect ADR 第 2.4 条、部署教程、数据字典

## 背景 / 约束 / 参考
- 项目规则 6（脱敏）、8（workspace scope + role 后端校验）；`docs/decisions/2026-11-02-deploy-connect.md`。
- Vikunja 核验 ADR 的“不保存用户的 Vikunja 密码”指 Connector 不用密码登录；这里保存的是给人看的备忘，Connector 仍只用 API Token。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 19:37 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 19:37 UTC · claude · 开始任务
- 2026-11-02 19:37 UTC · claude · 人类在对话中要求：在项目页（Vikunja）与自动化页（n8n）显示两者的登录账号密码，方便复制登录。做法：工作台加密保存，只给 admin 以上显示，默认遮住、点击显示才取明文并写审计、响应 no-store；可在页面填写 / 修改；connect 新建账号时自动保存。与 Vikunja 核验 ADR 的“不保存用户的 Vikunja 密码”不同（那是指 Connector 不用密码），在新 ADR 里说明
- 2026-11-02 20:05 UTC · claude · 后端（迁移 0019、服务、接口、密钥轮换）、connect save-login 与自动保存、deploy.sh 补存已接入组件的账号、前端两张卡片、测试与文档完成；顺带补上 apps/web/tests/browser-sprint5.cjs 缺的 /im/badges、/ai/tool-executions mock（之前新增的请求，测试不在 CI 里，一直没发现）
- 2026-11-02 20:15 UTC · claude · status in_progress → done：验收标准全部完成，PR #121
