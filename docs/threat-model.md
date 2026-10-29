# 威胁模型

> 状态：Sprint 0。风险清单来自 `docs/DEVELOPMENT_SPEC.md` 第 9.3 节；本文记录每项风险**目前**的处理情况，随 Sprint 更新。

| 风险 | 规范要求 | 当前状态 |
|---|---|---|
| 密钥泄露 | 密钥加密存储；日志和错误响应脱敏 | 连接串以 `SecretStr` 保存；依赖检查失败只记录异常类型；500 响应不含异常细节；访问日志不记录查询参数；422 不回显用户输入 |
| 日志注入（伪造 request_id） | — | 只接受格式合法的 `X-Request-ID`（字母数字与 `._-`，≤64 位），否则重新生成 |
| 未授权访问 / IDOR | 服务端 Policy 层校验 workspace + role | 尚无业务接口；Sprint 1 实现会话、CSRF 与角色策略 |
| SSRF | 集成 Base URL 校验 | 尚无 Connector；Sprint 3 起实现 |
| Webhook 伪造 | 签名 + 时间戳 + body 限制 | 尚未实现（Sprint 3） |
| AI Prompt Injection / 高危操作 | 工具策略校验 + 逐次审批 | 尚未实现（Sprint 6） |
| 宿主机接管 | 不挂载 docker.sock；API 不以 root 运行；数据库不暴露公网 | Compose 尚未实现（T-0006 落实） |
| 反向代理安全头 | CSP、HSTS、请求体限制 | 尚未实现（T-0006 落实） |
| API 文档暴露 | — | `APP_ENV=production` 时关闭 OpenAPI 与文档页 |
