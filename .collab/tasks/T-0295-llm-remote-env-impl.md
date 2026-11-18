---
id: T-0295
title: 大模型客户端：开发环境的“云端远程容器”实施
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api/app/domain/runner,services/api/app/domain/audit_labels.py,services/api/app/runner_agent,services/api/app/api/runner_api.py,services/api/app/models/runner.py,services/api/app/schemas/runner.py,services/api/app/modules/base.py,services/api/app/modules/registry.py,services/api/app/modules/llm_bridge,services/api/migrations,services/api/openapi.json,services/api/tests,services/llm-bridge,apps/web/src/modules/llm_bridge,apps/web/src/features/runner,apps/web/src/services/schema.d.ts,apps/web/tests,tests/e2e/llm_bridge_browser.cjs,docs/api-contract.md,docs/data-dictionary.md,docs/modules/README.md,docs/deployment.md
depends_on: T-0294
created: 2026-11-18
updated: 2026-11-18
---

# T-0295 大模型客户端：开发环境的“云端远程容器”实施

## 目标
人类 2026-11-18 在对话中要求（原话）：“给我根据这份方案 全面落实代码”。也就是按 T-0294 的方案 `docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md`（D1 ~ D8 按推荐）实现：
在大模型客户端 → 开发环境 → 新增开发环境里选“云端远程容器”，在容器服务器上建一个专用容器，让 Codex / Claude / CodeBuddy 的官方程序整个在容器里干活。

## 验收标准
- [x] 容器服务器：环境的归属（`owner_kind` / `owner_ref`）、不自动归档、空闲停止按模块设置；模块建的环境不算孤儿、不占实例配额，容器服务器页面上不能归档或删除（`409 MANAGED_BY_MODULE`）
- [x] 托管进程：`proc_start` / `proc_signal` / `proc_input` 三条指令，分块回传、按偏移确认、重启后续读；标准输入和凭据加密后只放 Redis、领指令时取一次，不落库；结束时的错误输出先脱敏再落库
- [x] 旧接入包：runner-agent 升到 0.4.0；工作台在建容器、起进程、按新镜像重建之前检查版本，旧版本直接提示“请在容器服务器页面重新部署接入包”，不派指令
- [x] 迁移 0079 与 Alembic 升级 → 降级 → 再升级的测试（已有数据的默认值、约束、降级后原样保留）
- [x] MCP 管理器：远程操作队列、远程进程；三个驱动的远程路径；Codex 每回合 `codex exec`，只有“可以执行命令”档；版本和管理器不一致时拒绝回合
- [x] 远程会话的后台作业：只在“可以执行命令”档、环境变量只给白名单、不写回合令牌、会话里的 `llm-task` 暂不可用，并有测试
- [x] 工作台：长轮询中转、远程容器的接口（建、改、操作、删、在容器里登录）、远程会话的文件浏览（容器里解析符号链接后必须在会话目录里，预览先脱敏）
- [x] 页面：新增开发环境弹窗的“运行位置”、开发环境页的容器信息条、聊天页的“云端”标记、容器服务器页的“申请方”和禁用的操作
- [x] 文档：接口契约、数据字典、模块说明、部署说明 25.10（含“重新部署接入包、重新部署管理器”）
- [x] 管理器 ruff / mypy / pytest；工作台 ruff / format / mypy / openapi / 相关测试；前端 lint / typecheck / test / build（CI 结果以 PR 的检查为准）

## 背景 / 约束 / 参考
- 编号由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留（[预留记录](https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6979130096)），和 T-0294 在同一个 PR #257 里。
- **越过 T-0224 的 paths**：codex 进行中的 T-0224（草稿 PR #203）占用容器服务器的 runner 文件和迁移。本任务是人类在对话中指派的，必须改到其中一部分：`runner.py`、`runner_hosts.py`、`runner_reclaim.py`、`runner_admin.py`、`api/runner_api.py`、`models/runner.py`、`schemas/runner.py`、runner-agent、迁移 0079。改动只限于托管进程和模块建的环境需要的部分，不改 T-0224 的功能。#257 合并后，集成会话会在 #50 告诉 codex：合入新的 main，迁移号接在 0079 后面。
- 与方案的偏差写在方案第 22 节：固定参考资料每回合作为系统提示带上、远程会话里暂不能用 `llm-task`、资源建好后不能改、上限是固定值、没有管理器级的远程容器总览、不支持 Kubernetes 类的容器服务器、默认网络是“全部外网”（订阅登录要连的域名还没核验完）。
- 需要真实账号验收：V1（Codex `exec` 的完整事件）、V4（刷新令牌会不会轮换）、V5（在容器里登录）、V7（`CODEBUDDY_IS_SANDBOX` 的效果）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 13:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-18 13:57 UTC · claude · 开始任务
- 2026-11-18 13:58 UTC · claude · 实现完成，和 T-0294 同在 PR #257。越过 T-0224 paths 的原因见“背景”：人类在对话中指派，只改托管进程和模块建的环境需要的部分。按 #235 集成会话的要求补了：迁移 0079 的升级 → 降级 → 再升级测试（tests/test_llm_bridge_remote_migration.py）；runner-agent 升到 0.4.0，建容器、起进程、重建前检查版本，旧接入包提示重新部署（test_runner_managed.py 两个新测试）；远程作业的环境变量白名单、不写回合令牌、档位限制的断言（services/llm-bridge/tests/test_remote.py）。本机：管理器 ruff / mypy / pytest 165 passed 4 skipped；工作台 ruff、format、mypy app、export_openapi --check 通过，runner 与远程容器相关 135 passed；前端 lint / typecheck / 935 tests / build 通过（上一轮）。
- 2026-11-18 13:58 UTC · claude · status in_progress → done：验收标准全部勾选；CI 结果以 PR #257 的检查为准
- 2026-11-18 15:44 UTC · claude · 集成审查（#235，固定 head 8cb2df6）交还：1 个阻塞 + 3 条建议 + 2 条小问题，全部在本 PR 修了，见方案第 22 节第 13 条。阻塞：模块的模板加 owner_kind（迁移 0079），mw- 开头的新模板名留给模块，智能体和管理员不能建、不能改模块的模板（409 MANAGED_BY_MODULE），模块不用别人建的同名模板（换备用名）；复现测试 test_module_templates_cannot_be_squatted_or_changed。建议：辅助命令在容器里带 timeout、keep_container 时超时不停容器，删会话目录先挪进回收目录再后台删；托管进程进程组没了或 KILL 之后没有结束记录时宽限后上报，回收按时限兜底记为结果未知；CodexDriver.version() 用白名单环境变量（测试在旧代码上失败）。小问题：模块定时任务删掉没有登记的远程容器（失败每 10 分钟重试）；新建远程容器 / 新增开发环境按 request_id 幂等，新建按工作空间排队、配额不会被并发绕过。
