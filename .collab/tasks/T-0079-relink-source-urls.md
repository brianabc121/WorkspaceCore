---
id: T-0079
title: 改了 Vikunja 打开地址后，已同步的项目与任务链接仍指向旧地址
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/projection.py,services/api/app/domain/integrations.py,services/api/app/commands/connect.py,services/api/tests/test_connect_command.py,services/api/tests/test_integrations_api.py,scripts/deploy.sh,docs/decisions/2026-11-02-deploy-connect.md
depends_on:
created: 2026-11-02
updated: 2026-11-02
---

# T-0079 改了 Vikunja 打开地址后，已同步的项目与任务链接仍指向旧地址

## 目标
项目与任务的「在 Vikunja 中打开」链接在同步时按当时的 `public_url` 存进 `external_resources.source_url`。
T-0078 的 `connect public-urls`（以及网页上修改浏览器地址）只改集成的 `public_url`，已同步的链接不变，
所以人类在项目页点开的仍是 `http://localhost:3456/projects/1`。

## 验收标准
- [x] 修改 Vikunja 集成的浏览器地址时，立即按新地址重写已同步的项目与任务链接（持投影锁，推送页面刷新事件）
- [x] `connect public-urls` 在地址没变时也刷新链接；新增 `connect relink`（只刷新、不改地址）
- [x] `deploy.sh connect` 在对外设置没变化时调用 `relink`，修好之前留下的旧链接
- [x] 测试：网页修改地址后项目与任务链接更新（接口测试）；`public-urls` 后链接更新；`relink` 修复旧链接且重复运行无改动

## 背景 / 约束 / 参考
- 立即可用的办法（不用等升级）：工作台「集成设置」里对 Vikunja 点「立即同步」，全量同步会按新地址重写链接
- `docs/decisions/2026-11-02-deploy-connect.md` 第 6 节补充

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-02 19:26 UTC · claude · 创建任务（owner: claude）
- 2026-11-02 19:26 UTC · claude · 开始任务
- 2026-11-02 19:26 UTC · claude · 人类报告：connect 把打开地址改成 https://vikunja.workspace123.uk 后，项目页「在 Vikunja 中打开」仍是 http://localhost:3456/projects/1。原因：链接在同步时按当时的 public_url 算好存进 external_resources.source_url，改 public_url 不重写；要等下一次全量同步才会更新
- 2026-11-02 19:30 UTC · claude · 测试：test_connect_command、test_connect_cli、test_integrations_api、test_tasks_api 共 74 项通过（含新增：网页改地址后项目 / 任务链接更新；relink 修复旧链接、重复运行无改动）；ruff、format、mypy（212 个文件）、shellcheck 通过
- 2026-11-02 19:30 UTC · claude · status in_progress → done：改打开地址时立即重写已同步的链接；relink 修复旧链接
