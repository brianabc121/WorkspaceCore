# 威胁模型

> 状态：Sprint 0 完成。风险清单来自 `docs/DEVELOPMENT_SPEC.md` 第 9.3 节；本文记录每项风险**目前**的处理情况，随 Sprint 更新。

| 风险 | 规范要求 | 当前状态 |
|---|---|---|
| 密钥泄露 | 密钥加密存储；日志和错误响应脱敏 | 连接串以 `SecretStr` 保存；依赖检查失败只记录异常类型；500 响应不含异常细节；访问日志不记录查询参数；422 不回显用户输入 |
| 日志注入（伪造 request_id） | — | 只接受格式合法的 `X-Request-ID`（字母数字与 `._-`，≤64 位），否则重新生成 |
| 未授权访问 / IDOR | 服务端 Policy 层校验 workspace + role | 尚无业务接口；Sprint 1 实现会话、CSRF 与角色策略 |
| SSRF | 集成 Base URL 校验 | 尚无 Connector；Sprint 3 起实现 |
| Webhook 伪造 | 签名 + 时间戳 + body 限制 | 尚未实现（Sprint 3） |
| AI Prompt Injection / 高危操作 | 工具策略校验 + 逐次审批 | 尚未实现（Sprint 6） |
| 宿主机接管 | 不挂载 docker.sock；API 不以 root 运行；数据库不暴露公网 | 未挂载 docker.sock；api / worker（uid 10001）、web（nginx uid 101）非 root、只读文件系统、去掉全部 capabilities、`no-new-privileges`；postgres / redis 只在内部网络，不映射宿主机端口；Caddy 默认只绑定 127.0.0.1 |
| 反向代理安全头 | CSP、HSTS、请求体限制 | Caddy 设置 CSP、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、COOP，去掉 `Server` 头，请求体上限 10 MB；HSTS 需在启用 HTTPS 后加入（见 deployment.md 第 6 节） |
| API 文档暴露 | — | `APP_ENV=production` 时关闭 OpenAPI 与文档页 |
| 就绪接口泄露内部状态 | `/readyz` 仅内部使用（规范第 6.1 节） | Caddy 只把内网来源的 `/readyz` 转发给 api，公网来源返回 404；响应只含 ok / error |
| 默认弱密码 | 不提供硬编码默认密码 | `make env` 为数据库密码、会话密钥、凭证加密密钥生成随机值；`.env` 权限 600 且被 git 忽略 |
