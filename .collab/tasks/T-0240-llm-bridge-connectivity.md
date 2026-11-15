---
id: T-0240
title: 大模型客户端：登录页显示管理器所在服务器到各平台的连通性与延迟（连不上 / 被平台拒绝）
owner: claude
status: done
priority: P2
branch: claude/trusting-euler-480tpv
paths: services/llm-bridge,services/api/app/modules/llm_bridge,services/api/tests,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/tests,apps/web/src/services/schema.d.ts,tests/e2e,docs
depends_on: T-0239
created: 2026-11-15
updated: 2026-11-15
---

# T-0240 大模型客户端：登录页显示管理器所在服务器到各平台的连通性与延迟（连不上 / 被平台拒绝）

## 目标
人类 2026-11-15 要求：有些服务器登不上 Claude / ChatGPT，连不上大模型平台时要在登录页上显示出来，同时显示延迟。

人类随后给了一台服务器的实测：`ping auth.openai.com` 通、延迟 2 ms，但 `curl https://auth.openai.com` 得到的是 OpenAI 的“Unable to load site”拦截页——网络通，但平台按 IP 把这台服务器挡了。所以不能只测“连不连得上”，要真的发一次请求看平台回的是什么。

## 验收标准
- [x] 管理器：对每个平台的地址发一次 HTTPS 请求（不带凭据），分清 可用 / 被平台拒绝 / 连不上，给出延迟；结果缓存一分钟；走容器的 HTTPS 代理
- [x] 工作台接口 `GET .../bridges/{id}/connectivity`（admin）；旧版本的管理器返回 supported=false
- [x] “平台与账号”里每个平台卡片显示各地址的状态和延迟，用不了时写明原因和怎么办；“重新检测连通性”
- [x] 管理器、工作台、前端的测试；验收脚本检查接口与页面

## 背景 / 约束 / 参考
- OpenAI 的拦截页按人类实测的样子识别；Claude、CodeBuddy 的“被拒绝”按通用规则（HTTP 403 且不是人机验证页）判断，没有逐个实测，可能误判。
- 地址清单来自各官方程序实际访问的域名（见 `services/llm-bridge/bridge/connectivity.py` 开头）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-15 09:07 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-15 09:07 UTC · human · owner unassigned → claude：人类在对话中指派（2026-11-15）：连不上大模型平台时在登录页显示，并显示延迟
- 2026-11-15 09:07 UTC · claude · 开始任务
- 2026-11-15 09:07 UTC · claude · status in_progress → done：实现与测试完成，随 PR #215 合并
