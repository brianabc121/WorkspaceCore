---
id: T-0219
title: 审批中心测试的倒计时断言在截止钟点是 01:29 / 01:30 时误匹配（main a22b8b9 Web CI）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: apps/web/tests/approval-center-redesign.test.ts
depends_on:
created: 2026-11-11
updated: 2026-11-11
---

# T-0219 审批中心测试的倒计时断言在截止钟点是 01:29 / 01:30 时误匹配（main a22b8b9 Web CI）

## 目标
main `a22b8b9` 的 Web CI（运行 42937738677）在 `npm run test` 失败：`tests/approval-center-redesign.test.ts:144`
的倒计时断言 `getByText(/01:(29|30)/)` 同时匹配到倒计时“01:30”和同一行“截止”后面 `<time>` 里的钟点 `01:29:47`
（测试在 01:18 UTC 运行，截止 = 现在 + 10 分钟时钟偏差 + 90 秒）。只要截止钟点里含 01:29 / 01:30 就会失败，
与当时合并的改动（#191 只改 e2e 脚本与文档）无关。第 146 行已经为 “11 点” 做过同样的处理，第 144 行漏了。

## 验收标准
- [x] 第 144 行的查询忽略 `<time>`（与第 146 行同样用 `ignore: 'script, style, time'`），只看倒计时。
- [x] 复现：用 `TZ=Etc/GMT+1` 在截止钟点落在 01:29 ~ 01:30 的时间窗口里同时跑修复前的副本和修复后的测试，修复前报
  `Found multiple elements with the text: /01:(29|30)/`（与 CI 一致），修复后通过。
- [x] 其他前端测试里没有同类按钟点匹配、又没排除 `<time>` 的文字查询。
- [x] `apps/web` 的 lint / typecheck / test / build / check:api 通过。

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-11 02:18 UTC · claude · 创建任务（owner: claude）
- 2026-11-11 02:18 UTC · claude · 开始任务
- 2026-11-11 02:21 UTC · claude · 第 144 行改为 getByText(/01:(29|30)/, { ignore: 'script, style, time' })。复现：02:17:36 UTC 用 TZ=Etc/GMT+1 同时跑修复前副本（失败，Found multiple elements，与 CI 一致）和修复后测试（通过）。扫描 apps/web/tests 无同类查询。lint / typecheck / check:api / test（77 文件 757 项）/ build 全部通过
- 2026-11-11 02:21 UTC · claude · status in_progress → done：测试断言只看倒计时；复现与前端全套检查通过。按 D-0004 在 PR 的 CI 全部通过后合并
