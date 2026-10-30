# 威胁模型

> 状态：Sprint 1 进行中。风险清单来自 `docs/DEVELOPMENT_SPEC.md` 第 9.3 节；本文记录每项风险**目前**的处理情况，随 Sprint 更新。

| 风险 | 规范要求 | 当前状态 |
|---|---|---|
| 密钥泄露 | 密钥加密存储；日志和错误响应脱敏 | 连接串以 `SecretStr` 保存；依赖检查失败只记录异常类型；500 响应不含异常细节；访问日志不记录查询参数；422 不回显用户输入；第三方凭证只存 AES-256-GCM 密文（绑定用途）或 `env:WS_SECRET_*` 引用（T-0011）；审计 metadata 写入前脱敏 |
| 日志注入（伪造 request_id） | — | 只接受格式合法的 `X-Request-ID`（字母数字与 `._-`，≤64 位），否则重新生成 |
| 未授权访问 | 私有接口要求有效会话 | 会话 Token 只存 SHA-256；绝对有效期 30 天、闲置 7 天失效；登出撤销；停用账户的会话立即失效（T-0010） |
| IDOR / 越权 | 服务端 Policy 层校验 workspace + role | 统一 Policy（`app/domain/policy.py`）：非成员与不存在的工作空间返回相同的 404，角色不够 403；会话只能撤销自己的；两用户、两工作空间的越权测试（T-0011）；仪表盘与组件按 `X-Workspace-Id` 限定，别的工作空间的资源一律 404，viewer 写入 403（T-0015，A03） |
| 组件内容注入（XSS） | — | 常用链接只接受 http(s)，拒绝 `javascript:` 等；便签按纯文本显示；读取的配置由前端再校验，未知类型显示错误卡片（T-0014 / T-0015） |
| CSRF | CSRF Token + Origin / Referer 校验 | `ws_csrf` = HMAC(SESSION_SECRET, 会话 Token)；需登录的写请求校验 `X-CSRF-Token`；所有写请求校验来源；Cookie `SameSite=Lax`，会话 Cookie HttpOnly（T-0010） |
| 暴力破解 / 撞库 | 登录限流与失败延迟 | 账户 + IP 双维度限流，失败延迟 0.5 秒；Argon2id；账户不存在与密码错误响应与耗时一致（T-0010）；修改密码时当前密码错误同样按用户限流（T-0011） |
| 会话被盗用 | 注销和改密码撤销既有会话 | 会话列表与单个撤销；修改密码撤销该用户全部会话并只为当前客户端签发新会话；登录与改密按用户行锁串行，并发的旧密码登录拿不到会话（T-0011） |
| 操作不可追溯 / 日志被篡改 | 审计日志追加写 | `audit_logs` 记录登录成败、登出、改密码、撤销会话、修改设置；触发器拒绝 UPDATE；IP 只存带密钥的哈希（T-0011） |
| SSRF | 集成 Base URL 校验 | 尚无 Connector；Sprint 3 起实现 |
| Webhook 伪造 | 签名 + 时间戳 + body 限制 | 尚未实现（Sprint 3） |
| AI Prompt Injection / 高危操作 | 工具策略校验 + 逐次审批 | 尚未实现（Sprint 6） |
| 宿主机接管 | 不挂载 docker.sock；API 不以 root 运行；数据库不暴露公网 | 未挂载 docker.sock；api / worker（uid 10001）、web（nginx uid 101）非 root、只读文件系统、去掉全部 capabilities、`no-new-privileges`；postgres / redis 只在内部网络，不映射宿主机端口；Caddy 默认只绑定 127.0.0.1 |
| 反向代理安全头 | CSP、HSTS、请求体限制 | Caddy 设置 CSP、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、COOP，去掉 `Server` 头，请求体上限 10 MB；HSTS 需在启用 HTTPS 后加入（见 deployment.md 第 6 节） |
| API 文档暴露 | — | `APP_ENV=production` 时关闭 OpenAPI 与文档页 |
| 就绪接口泄露内部状态 | `/readyz` 仅内部使用（规范第 6.1 节） | Caddy 只把内网来源的 `/readyz` 转发给 api，公网来源返回 404；响应只含 ok / error |
| 默认弱密码 / 抢注管理员 | 不提供硬编码默认密码 | `make env` 为数据库密码、会话密钥、安装凭据、凭证加密密钥生成随机值；`.env` 权限 600 且被 git 忽略；首次安装必须提供 `SETUP_TOKEN` 且只能成功一次 |
