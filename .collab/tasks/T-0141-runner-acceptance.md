---
id: T-0141
title: 运行服务器 R6 验收：CI 里真实 Docker + gVisor + runner-agent、场景 26、浏览器验收
owner: claude
status: in_progress
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github,docs,infra,services/api/app/runner_bundle,services/api/app/domain/runner_admin.py,services/api/tests
depends_on: T-0139, T-0140
created: 2026-11-06
updated: 2026-11-07
---

# T-0141 运行服务器 R6 验收：CI 里真实 Docker + gVisor + runner-agent、场景 26、浏览器验收

## 目标
在 CI 里用真实 Docker + gVisor + runner-agent 验收运行服务器（方案第 15 节）。

## 验收标准
- [ ] CI 的 runner 上装 gVisor release-20261019.0，runner-agent 连工作台
- [ ] 场景 26：一次性测试跑完即删；会话按 Issue 关闭删除；常驻停止后唤起、归档后重建；gvisor 与 runc 都能跑
- [ ] 浏览器验收：运行服务器页面
- [ ] gVisor 实测结果补进核验记录

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
- 2026-11-07 01:10 UTC · claude · 开始任务
- 2026-11-07 01:10 UTC · claude · 验收写好、待 CI 实测：ci-infra agents 任务在启动服务前用接入包里的 install-gvisor.sh 装 gVisor（之后不再重启 Docker）；agents_acceptance.py 场景 26（接入包原样以 root 运行 install.sh；模板构建；甲经真实 OpenClaw 调 env_create 跑一次性测试、跑完即删；会话环境 allowlist 出口、reuse_key 复用、issue_closed 删除；常驻 runc 停止后唤起、归档后从快照重建并重放初始化步骤；gvisor 与 runc 的内核对比）；runner_browser.cjs（页面上添加外网入口的运行服务器、SSH 部署到上线、各标签、卸载、吊销、手机宽度）。顺带修了两处产品问题（paths 因此加上 runner_bundle 与 runner_admin.py）：重装时 Docker 已认得 runsc 就不再重启 Docker（以免停掉运行中的环境）；外网入口没有域名时接入包把入口主机名映射到 AGENT_PUBLIC_ADDRESS（与 OpenClaw 接入包一致）。
- 2026-11-07 01:49 UTC · claude · 第一轮 CI：场景 26 前四项在真实 gVisor 上通过；出口代理（gVisor DNS，见 T-0135）与 issue_closed（会话环境宽限 5 分钟，验收等得太短）两项失败，已修：验收先把宽限期设成 1 分钟，再分别检查待删除与删除。compose 任务的完整冒烟发现 GET /runner/host/ssh 没有运行服务器时 404，已改成空状态。
