---
id: T-0133
title: 外网模式实例的模型调不到工作台 MCP 工具：隧道改为非回环地址（OpenClaw SSRF 拦截 127.0.0.1）
owner: claude
status: todo
priority: P1
branch:
paths: services/api,infra,tests/e2e,docs
depends_on:
created: 2026-11-06
updated: 2026-11-06
---

# T-0133 外网模式实例的模型调不到工作台 MCP 工具：隧道改为非回环地址（OpenClaw SSRF 拦截 127.0.0.1）

## 目标
外网模式的 OpenClaw 实例现在能收发 IM，但模型调不到工作台的任何 MCP 工具：OpenClaw 2026.9.36 的 SSRF 防护拦下了解析到回环地址的 MCP 地址，
而外网模式把工作台外网域名在容器里指向 127.0.0.1（本机隧道）。要让外网模式的模型也能用工作台工具，同时不降低现有的隔离（明文只在本机、双向证书）。

## 验收标准
- [ ] 方案经人类确认（会改接入包的网络结构，可能影响已部署的外网实例的升级）
- [ ] 外网模式实例的模型经 Tool Search 调工作台工具成功（agents 任务里乙经模型调用，而不是直接调 MCP）
- [ ] 内网模式不受影响；IM、取密钥、上报、唤醒、终止等经隧道的请求照常
- [ ] 已部署的外网实例的升级路径（重新签发接入包 / SSH 重新部署）写进 deploy-guide
- [ ] 去掉 deploy-guide 里的“已知问题”提示

## 背景 / 约束 / 参考
- 查明经过：`docs/decisions/2026-11-04-agent-messaging-verification.md` 6.4（T-0125，PR #161 的 CI agents 任务）。
- 已排除：把 MCP 地址改成 `http://127.0.0.1:<端口>/mcp` 再用请求头带回 Host——外网入口 Caddy 开了双向证书，要求 Host 与 SNI 一致；锁定镜像里 undici 8.10.2 的 fetch 会忽略自定义 Host（实测）。
- 可选方向（待核验后定）：
  1. 隧道容器改到一个专用的 Docker 网络（固定的私有地址，例如 172.x），OpenClaw 仍用 host 网络，经宿主机路由访问；`extra_hosts` 把外网域名指向这个私有地址（OpenClaw 对配置的 MCP 主机名放行私有地址，只拦回环）。暴露面与现在的 127.0.0.1 相当（只有本机能到），要处理子网冲突。
  2. MCP 单独直连外网入口：OpenClaw 的 `mcp.servers.<name>.clientCert` / `clientKey` 支持双向证书，但外网域名在容器里已经指向 127.0.0.1，同一个主机名不能同时指向两处，要另起一个主机名或改其他请求的地址。
- 不得为此放宽 OpenClaw 的工具限制或安全检查（`docs/AGENTS.md` 第 8 节）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 14:28 UTC · claude · 创建任务（owner: claude）
