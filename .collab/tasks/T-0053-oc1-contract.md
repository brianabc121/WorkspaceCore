---
id: T-0053
title: OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: docs/decisions,docs/api-contract.md,services/api/app/schemas,services/api/app/api,services/api/openapi.json,apps/web/src/services/schema.d.ts,services/api/tests/test_contract.py
depends_on: T-0052
created: 2026-10-31
updated: 2026-10-31
---

# T-0053 OC-1 V2 接口契约：实例、密钥、IM、审批、/agent-api、MCP 工具、内部回调（S6-01 契约）

## 目标
给 OC-2 ~ OC-5 定接口契约（方案第 4、5 节），前后端和测试按契约并行。

## 验收标准
- [x] 契约 ADR：管理员 API（实例、密钥、IM、审批）、`/agent-api`、MCP 工具的参数和返回、内部回调，以及错误码
- [x] Pydantic schema 与接口桩、`openapi.json`、`apps/web/src/services/schema.d.ts`、`docs/api-contract.md` 已更新
- [x] 契约测试通过；`make lint`、`make test` 通过

## 背景 / 约束 / 参考
- 依据：`docs/v2/TRANSFORMATION_PLAN.md`；论证与上游源码核对见 `docs/v2/OPENCLAW_PLAN.md`
- 拆自 T-0047（人类 2026-10-31 决定按推荐执行）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-10-31 16:14 UTC · claude · 创建任务（owner: claude）
- 2026-10-31 17:45 UTC · claude · 开始任务
- 2026-10-31 17:57 UTC · claude · 契约 ADR docs/decisions/2026-10-31-v2-oc1-contract.md；schema（agents/im/ai_tools/openim_callbacks）与 48 个管理员端点、3 个实例端点的 501 桩；网关与 OpenIM 回调路由不进 OpenAPI；MCP 工具清单 TOOLS（15 读 6 写 3 高危）。越出 paths 的文件（都不在其他进行中任务的范围内）：services/api/app/main.py（挂载实例接口路由）、apps/web/src/features/events/useWorkspaceEvents.ts（SSE 新 topic 的映射，否则前端 typecheck 失败）、infra/openclaw/bin/myworkspace-openclaw-start（im-token 响应改用 ws_addr/api_addr）。make lint-api、pytest（含集成，412 passed）、web lint/typecheck/test/build 通过
- 2026-10-31 17:57 UTC · claude · status in_progress → done：OC-1 契约完成：ADR、schema、接口桩、OpenAPI、schema.d.ts、api-contract.md、契约测试
- 2026-10-31 18:11 UTC · claude · PR #77 的 ci-infra 在 Sprint 4 浏览器验收失败：测试先把模拟 GitHub 切到限流，再点“手动同步”；CI 每分钟一次的对账正好在两者之间碰到限流，按钮按设计被禁用，点击超时（main 上同一检查是绿的，属于测试与定时对账的竞态）。修复 tests/e2e/sprint4_browser.cjs：按钮已禁用时说明同步已经发生，直接检查开发者页的限流提示。越出 paths，原因同上
- 2026-10-31 18:30 UTC · claude · 第二轮 ci-infra 在 make smoke-full 失败，是本 PR 引起的：full_smoke.cjs 按 openapi.json 遍历全部 GET 接口要求 200，新增的 V2 桩返回 501。修复：探测到 501 NOT_IMPLEMENTED 时单独列出不算失败（其他非 200 照常失败），页面检查开始前不把这些 URL 的 501 记为控制台错误；页面阶段的 501 照常算失败。本地把分支 API 代码只读挂进本会话自己的 api 容器后运行 full_smoke：全部通过（27 个读接口 200，10 个已声明未实现）
- 2026-10-31 18:43 UTC · claude · 第三轮 ci-infra 的 Sprint 4 浏览器验收又撞上整分钟的定时对账：这次点击成功，但对账正持有同步锁，手动同步返回 409 SYNC_IN_PROGRESS，“同步已排队”没出现。改为三种情况都接受（按钮已禁用 / 已排队 / 同步正在进行），开发者页的限流提示最多等 90 秒（最迟下一次对账会碰到限流，模拟限流持续 120 秒）。本地运行 sprint4_browser.cjs 全部通过
