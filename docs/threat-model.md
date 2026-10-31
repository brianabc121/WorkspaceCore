# 威胁模型

> 状态：Sprint 6（2026-10-31 更新）。风险清单来自 `docs/DEVELOPMENT_SPEC.md` 第 9.3 节；本文记录每项风险**目前**的处理情况，随 Sprint 更新。
> 全部接口的未登录 / CSRF / 非成员 / viewer 写入检查由 `services/api/tests/test_security_matrix.py` 按 OpenAPI 自动覆盖（T-0043）。

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
| SSRF | 集成 Base URL 校验 | Connector 只允许 http(s)；解析到私有、回环、链路本地地址一律拒绝，只有 `INTEGRATION_ALLOWED_HOSTS` 里的 Compose 内部主机例外；不跟随重定向（T-0020）。**防 DNS 重绑定**：检查通过后直接连接检查过的 IP，`Host` 头与 TLS SNI 保留原主机名，证书仍按主机名校验（T-0043）。每个请求有超时、读请求有限次重试、熔断按集成计数 |
| Webhook 伪造 | 签名 + 时间戳 + body 限制 | Vikunja Webhook：`X-Vikunja-Signature` 按原始请求体做 HMAC-SHA256、常量时间比较；集成不存在 / 停用 / 签名不对一律 401；请求体上限 256 KiB；以请求体哈希去重写入 Inbox，只保存事件名与 ID，处理时以 Vikunja API 读到的当前状态为准（payload 不信任，重放与乱序无害）；周期对账兜底（T-0019、T-0021）。Vikunja 2.7.1 的 Webhook 不带时间戳（核验 ADR），所以用“只认当前状态”代替时间戳窗口 |
| AI Prompt Injection / 高危操作 | 工具策略校验 + 逐次审批 | **AI 尚未实现**：S6-01 ~ S6-03 等人类选定 AI 服务后再做（T-0047），目前没有任何 AI 入口。自动化（n8n）已按策略校验：只能触发白名单里已启用的工作流，参数按登记的定义校验，member 以上才能触发，必须带 `Idempotency-Key`，结果未知时不自动重发；连接与白名单只有 admin 能改，API Key 与共享密钥只写不读（T-0037） |
| 宿主机接管 | 不挂载 docker.sock；API 不以 root 运行；数据库不暴露公网 | 未挂载 docker.sock；api / worker（uid 10001）、web（nginx uid 101）非 root、只读文件系统、去掉全部 capabilities、`no-new-privileges`；postgres / redis 只在内部网络，不映射宿主机端口；Caddy 默认只绑定 127.0.0.1 |
| 反向代理安全头 | CSP、HSTS、请求体限制 | Caddy 设置 CSP、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、COOP，去掉 `Server` 头，请求体上限 10 MB；HSTS 需在启用 HTTPS 后加入（见 deployment.md 第 6 节） |
| API 文档暴露 | — | `APP_ENV=production` 时关闭 OpenAPI 与文档页 |
| 就绪接口泄露内部状态 | `/readyz` 仅内部使用（规范第 6.1 节） | Caddy 只把内网来源的 `/readyz` 转发给 api，公网来源返回 404；响应只含 ok / error |
| 依赖与镜像漏洞、密钥进入仓库 | 安全扫描（规范第 12 节） | `ci-security.yml`：依赖（pip-audit、npm audit）有高危就失败；gitleaks 扫描整个 git 历史，误报按指纹登记；本仓库构建的镜像有已修复的高危漏洞就失败，第三方镜像只报告、发布前跟进（T-0044）。所有镜像按 tag + digest 锁定（`docs/versions.md`） |
| 数据丢失 | 加密备份、异地、定期恢复演练（规范 11.4） | `make backup`：restic 加密仓库，按 7 / 30 / 90 日保留；`make restore-verify` 在隔离环境恢复并做冒烟与迁移检查，CI 每次都演练（A15，T-0042）。`.env` 与备份分开保存 |
| 默认弱密码 / 抢注管理员 | 不提供硬编码默认密码 | `make env` 为数据库密码、会话密钥、安装凭据、凭证加密密钥生成随机值；`.env` 权限 600 且被 git 忽略；首次安装必须提供 `SETUP_TOKEN` 且只能成功一次 |
