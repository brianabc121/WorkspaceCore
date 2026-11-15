---
id: T-0239
title: 大模型客户端：登录时平台出错要如实显示原因，不说成“MCP 管理器连不上”
owner: claude
status: done
priority: P2
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,docs
depends_on: T-0238
created: 2026-11-15
updated: 2026-11-15
---

# T-0239 大模型客户端：登录时平台出错要如实显示原因，不说成“MCP 管理器连不上”

## 目标
人类 2026-11-15 反馈：在“平台与账号”里给 ChatGPT / Codex 点“获取验证码”，页面提示“MCP 管理器暂时连不上”，而管理器其实是通的。

原因：登录时平台那边没成功（向 OpenAI 申请设备码失败、超时），管理器带着原因回 503；工作台把 5xx 一律当成“管理器连不上”，真正的原因被盖住，还会计入熔断。

## 验收标准
- [x] 管理器：平台那边没成功时带着原因回 424，不回 5xx
- [x] 工作台：管理器的错误应答里有原因时照它报（兼容还在回 503 的旧版本管理器）；连着失败不会把管理器当成坏了
- [x] Codex 设备码登录失败的说明里写明原因和常见的两种情况
- [x] 管理器、工作台的测试

## 背景 / 约束 / 参考
- 用户服务器上失败的具体原因要等升级后看页面上的说明；最可能是那台服务器访问不了 OpenAI。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 08:53 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 08:54 UTC · human · owner unassigned → claude：人类在对话中反馈并要求检查（2026-11-15）：点“获取验证码”提示 MCP 管理器连不上
- 2026-11-15 08:54 UTC · claude · 开始任务
- 2026-11-15 08:54 UTC · claude · status in_progress → done：修复与测试完成，随本 PR 合并
