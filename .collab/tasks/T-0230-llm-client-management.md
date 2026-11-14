---
id: T-0230
title: 大模型客户端管理页面：多个 MCP 管理器、每个管理器选控制的平台并单独登录账号
owner: claude
status: done
priority: P1
branch: claude/trusting-euler-480tpv
paths: apps/web/src/modules/llm_bridge,apps/web/tests,services/api/app/modules/llm_bridge,services/api/migrations,services/api/tests,services/llm-bridge,infra/llm-bridge,tests/e2e,.github/workflows/ci-llm-bridge.yml,Makefile,docs
depends_on:
created: 2026-11-13
updated: 2026-11-14
---

# T-0230 大模型客户端管理页面：多个 MCP 管理器、每个管理器选控制的平台并单独登录账号

## 目标
人类 2026-11-13 在对话中要求：MCP 设计好后，工作台再加一个“大模型客户端管理”页面。在这个页面上可以添加一个或多个
大模型 MCP 工具（例如 1 号 MCP 管理器控制 Claude / WorkBuddy，2 号控制 Claude / WorkBuddy / Codex）；
每个 MCP 管理器单独维护一套大模型账号，用户在工作台上给每个管理器登录各平台的账号。

已有的 `llm_bridge` 模块已经能登记多座会话桥、每座桥单独登录账号。本任务在它上面补齐：
每个管理器选控制的平台、页面按“MCP 管理器”重新组织、同机再起一个管理器的部署命令、按实例限定能用哪些管理器。
方案见 `docs/v2/LLM_BRIDGE_PLAN.md` 第 19 节。

## 验收标准
- [x] 会话桥：设置 `platforms`（控制的平台，默认全部）。没勾选的平台不能登录、不能新建会话和发送、查不到模型和环境；已有的登录和会话记录保留；测试覆盖
- [x] 工作台后端：添加管理器时可带 `platforms`（先写进会话桥并验证地址和令牌，失败不登记）；设置里可改；状态带 `platforms` 和每个账号、通道的 `enabled`
- [x] 按实例限定管理器：`llm_bridge_grants.bridge_ids`（迁移 0068，带 downgrade）；NULL 全部、空列表一个都不能用；删除管理器不会放宽权限；测试覆盖
- [x] 智能体工具：`bridge` 只在能用多个管理器时必填；不填时 `llm_bridge_status` 列出每一个和各自控制的平台；没授权的管理器按不存在处理
- [x] 页面“大模型客户端管理”：管理器卡片（控制的平台与各平台是否已登录）、添加时勾选平台、在“平台与账号”里改控制的平台、实例权限里选允许的管理器；前端测试覆盖
- [x] 部署：`make llm-bridge-up N=2` 再起一个管理器（自己的项目、数据卷、令牌、端口、内部网络里的名字）；部署指南第 25 节
- [x] 浏览器验收 `tests/e2e/llm_bridge_browser.cjs` 改成两个管理器（1 号 Claude / WorkBuddy，2 号 Codex / Claude / WorkBuddy），本机对两个真实的会话桥跑通
- [x] CI：`ci-llm-bridge` 的 `workspace` job（同机起两个管理器、名字与令牌互不串、冒烟、浏览器验收）和其余检查通过（提交 `2f1a375`：16 项检查全部成功，`workspace` 是运行 43473335254）
- [x] 文档：方案第 19 节、部署指南第 25 节、接口文档、数据字典

## 背景 / 约束 / 参考
- 没有另起模块：页面上的“MCP 管理器”就是一座登记的会话桥，接口、表名、工具名不变。
- 例外九（`docs/AGENTS.md`）没有改动：新增的“允许的 MCP 管理器”只是把已有的授权收得更细。
- 没有做“在页面上直接起容器”：工作台没有在服务器上起容器的途径，这是新的权限，要人类决定（方案第 19 节末尾）。
- 需要真实账号的验收（各平台的登录流程、真实回合）仍然需要人类登录。
- 本会话没有可用的 Docker 构建：同机部署两个管理器只能在 CI 上实跑。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 16:11 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 16:42 UTC · claude · 开始任务
- 2026-11-13 16:42 UTC · claude · 在本地工作树里完成：会话桥的 platforms 设置（24 passed）；工作台后端登记时选平台、bridge_ids（迁移 0068）、llm_bridge_status 多管理器汇总（模块 / 契约 / 模块框架测试 59 passed）；页面改成大模型客户端管理（前端 llm_bridge 16 项通过，lint / typecheck / check:api 通过）；make llm-bridge-up N=2、Compose 服务名改 mcp-manager、CI workspace job 起两个管理器；浏览器验收脚本改成两个管理器，本机对两个真实的会话桥（真实 codex / codebuddy，不接 Claude 程序）15 步全部通过。等 PR #200（T-0229）合并后把分支重置到 main 再推送、开 PR。
- 2026-11-13 16:42 UTC · claude · 指派记录：这个需求是人类 2026-11-13 在对话中直接提出并交给 claude 的（任务创建时 owner 就写成了 claude，所以没有单独的 owner 变更记录）。
- 2026-11-13 17:26 UTC · claude · PR #205 第一轮 CI：llm-bridge job 的 test_codex_account_and_models 失败（登录状态一直是 pending）。根因不在测试而在 Codex 驱动：account/login/completed 紧跟着 account/login/start 的应答到达、早于 login() 记下“进行中”时，通知被丢掉，登录状态永远停在进行中（T-0227 就有，机器慢时才出现）。修法：按 loginId 记下早到的完成通知，login() 拿到应答后认领（字段按锁定版本 0.169.0 的协议定义核对过）。假程序加了“应答和通知同一次写入”的模式稳定复现：修之前新用例 3/3 失败，修之后通过；CPU 占满时驱动测试重复 15 遍 0 失败（修之前 12 遍 5 失败）。同一个文件里另外三处“等固定时长再取消”改成等回合真正开始。
- 2026-11-13 17:29 UTC · claude · PR #205 第一轮 CI 的 workspace job：同机起两个管理器、内部网络里的名字和令牌互不串、两个容器的冒烟都通过；浏览器验收在第一步失败（停用时侧栏不应有入口）。原因是脚本的竞态：模块状态读到之前前端先按启用显示入口，脚本登录后立刻去数。改成重新载入并等 /api/v1/modules 返回后再确认入口消失；本机对两个真实的会话桥连续跑通两遍。
- 2026-11-14 02:29 UTC · claude · status in_progress → done：提交 2f1a375 上 16 项检查全部成功：workspace job（make llm-bridge-up 与 N=2 同机起两个管理器、内部网络里的名字和令牌互不串、两个容器的冒烟、15 步浏览器验收、验收后的冒烟）、llm-bridge（含新的驱动回归用例）、api（pytest 通过，df24ae0 上那次 Redis 连接超时没有复现）、agents、compose、web 等。没有做的：页面上一键部署管理器（需要人类决定）；各平台真实账号的验收仍留给人类。
