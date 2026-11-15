---
id: T-0247
title: 大模型客户端改进第 1 期：先修正的 7 处（平台与环境权限、密钥拦截、通知、幂等、进度）（V2）
owner: claude
status: done
priority: P0
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/migrations,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e,docs
depends_on:
created: 2026-11-15
updated: 2026-11-15
---

# T-0247 大模型客户端改进第 1 期：先修正的 7 处（平台与环境权限、密钥拦截、通知、幂等、进度）（V2）

## 目标
落实 `docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md` 第 2 节：7 处和已批准的规则（例外九）或原方案对不上的地方，以及两处过时的文档。

## 验收标准
- [x] 0-1 平台限制跟着会话走：工作台把允许的平台带给管理器，管理器按会话所属平台检查
- [x] 0-2 实例权限加“可用的开发环境”，默认一个都不勾；页面上可以勾选；有迁移
- [x] 0-3 发给平台的内容先过密钥扫描（智能体和人都是），命中就不发、不存
- [x] 0-4 智能体接手别人的会话发消息时通知管理员，链接到那个会话
- [x] 0-5 被发送限额拒绝时通知管理员（同一个实例一小时一条）
- [x] 0-6 新建会话收幂等号；没等到回答时的提示按工具给出
- [x] 0-7 运行中回合的进度按回合取最新一条
- [x] 两处过时的文档改正；`docs/api-contract.md`、`docs/deployment.md` 更新
- [x] 管理器、后端、前端的 lint / typecheck / test / build / check:api 通过

## 背景 / 约束 / 参考
（可选）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 19:42 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 19:42 UTC · human · owner unassigned → claude：人类在对话中指派：根据改进方案全面落实代码（第 1 期）
- 2026-11-15 19:42 UTC · claude · 开始任务
- 2026-11-15 19:42 UTC · claude · 迁移 0071 给 llm_bridge_grants 加 environment_ids，已有授权升级后是空列表：智能体暂时不能新建开发会话，要管理员重新勾选（有意收紧，按例外九）。和方案同一个 PR（#219）一起合并，省一轮 CI。
- 2026-11-15 19:42 UTC · claude · status in_progress → done：第 1 期的 7 处修正完成
