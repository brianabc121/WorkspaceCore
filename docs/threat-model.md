# 威胁模型

> 状态：Sprint 6（2026-11-01 更新，含 V2 的 OpenClaw / OpenIM）。风险清单来自 `docs/DEVELOPMENT_SPEC.md` 第 9.3 节与
> `docs/v2/TRANSFORMATION_PLAN.md` 第 7 节；本文记录每项风险**目前**的处理情况，随 Sprint 更新。
> 全部接口的未登录 / CSRF / 非成员 / viewer 写入检查由 `services/api/tests/test_security_matrix.py` 按 OpenAPI 自动覆盖（T-0043）；
> V2 的反 Prompt Injection、越权与绕过审批测试在 `services/api/tests/test_oc6_security.py`（T-0058），
> 真实 OpenClaw + OpenIM 的多实例端到端在 `tests/e2e/agents_acceptance.py`（方案第 8 节 14 个场景，CI 的 agents 任务，T-0059）。

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
| AI Prompt Injection / 高危操作 | 工具策略校验 + 逐次审批 | **V2（OpenClaw）**：实例只能经 MCP 调工作台工具，工具按实例的 `scope`（read / read_write）列出，按“登记人角色与 `max_role` 中较低的一个”检查角色；写工具与高危工具一律先生成待审批记录（A14），不执行；批准要 owner / admin，高危操作还要输入密码（二次认证，错误次数有上限），实例不能审批自己的请求，聊天里回复“同意”不算批准；参数规范化后算哈希，同样参数同时只有一条待审批，执行前用带密钥的哈希核对参数没被改过（被改则不执行，`ARGUMENTS_TAMPERED`）；待审批 15 分钟过期；执行时按目标的版本检查，权限被收回则拒绝；摘要由工作台按参数生成，不采用 AI 的文字；审批卡片标明来源会话与触发者，来源由工作台推断、不信任 AI 自报。OpenClaw 智能体只开工作台工具与 IM 发消息工具（`tools.exec.security = deny`，不挂 Docker socket，见接入包 CHECKLIST.md）。测试：`test_oc6_security.py`（任务文本诱导、别的实例的群消息冒充来源、参数篡改、并发审批、错误密码锁定、跨工作空间）、多实例端到端场景 9 / 10。自动化（n8n）沿用 T-0037 的白名单与参数校验 |
| 宿主机接管 | 不挂载 docker.sock；API 不以 root 运行；数据库不暴露公网 | 未挂载 docker.sock；api / worker（uid 10001）、web（nginx uid 101）非 root、只读文件系统、去掉全部 capabilities、`no-new-privileges`；postgres / redis 只在内部网络，不映射宿主机端口；Caddy 默认只绑定 127.0.0.1 |
| 反向代理安全头 | CSP、HSTS、请求体限制 | Caddy 设置 CSP、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、COOP，去掉 `Server` 头，请求体上限 10 MB；HSTS 需在启用 HTTPS 后加入（见 deployment.md 第 6 节） |
| API 文档暴露 | — | `APP_ENV=production` 时关闭 OpenAPI 与文档页 |
| 就绪接口泄露内部状态 | `/readyz` 仅内部使用（规范第 6.1 节） | Caddy 只把内网来源的 `/readyz` 转发给 api，公网来源返回 404；响应只含 ok / error |
| 依赖与镜像漏洞、密钥进入仓库 | 安全扫描（规范第 12 节） | `ci-security.yml`：依赖（pip-audit、npm audit）有高危就失败；gitleaks 扫描整个 git 历史，误报按指纹登记；本仓库构建的镜像有已修复的高危漏洞就失败，第三方镜像只报告、发布前跟进（T-0044）。所有镜像按 tag + digest 锁定（`docs/versions.md`） |
| 数据丢失 | 加密备份、异地、定期恢复演练（规范 11.4） | `make backup`：restic 加密仓库，按 7 / 30 / 90 日保留；`make restore-verify` 在隔离环境恢复并做冒烟与迁移检查，CI 每次都演练（A15，T-0042）。`.env` 与备份分开保存 |
| 默认弱密码 / 抢注管理员 | 不提供硬编码默认密码 | `make env` 为数据库密码、会话密钥、安装凭据、凭证加密密钥生成随机值；`.env` 权限 600 且被 git 忽略；首次安装必须提供 `SETUP_TOKEN` 且只能成功一次 |
| **V2：实例凭证泄露** | 只存哈希、绑定入口、外网加双向证书、一键停用 | 实例令牌只存 SHA-256，绑定登记时的入口（内网 / 外网），用错入口一律 401；外网模式还要客户端证书（工作台内部 CA 签发，90 天），吊销按证书指纹立即生效、不依赖 CRL；重新生成接入包时旧凭证再保留 24 小时；停用实例或关掉工作空间的总开关后，认证立即失败并强制 IM 下线；最近使用时间与 IP 可查；外网入口按 IP 限制认证失败次数。端到端场景 6、7 |
| **V2：OpenClaw 主机被攻破** | 损失限定在授权范围 | 只能取到授权给这个实例的密钥（每次取用都记录，`agent_secret_accesses`），只能调它 `scope` 内的工具，写操作要人工审批；接入网关只放行白名单路由，校验请求体里的用户 / 会话属于这个实例；停用后 MCP、取密钥、IM 立即失效（场景 6） |
| **V2：工作台被攻破 / 密钥库泄露** | 加密存储、可轮换、管理员 API 不返回明文 | 密钥值只存 `enc:v1:<key_id>:` 密文（AES-256-GCM，用途绑定到密钥与版本），轮换后旧版本的密文立即销毁；凭证加密密钥可轮换（`rotate_credentials`，`docs/deployment.md` 第 6.1 节）；管理员接口只返回元数据；OpenIM 的 `secret` 与 imAdmin 令牌只在服务器端；内部 CA 私钥只以密文存在数据库 |
| **V2：OpenIM 接口的权限缺陷** | 实例的请求必须经接入网关；浏览器不直连 OpenIM | 使用打过补丁的 OpenIM 镜像（修复会话、搜索等接口不检查调用者，OC-0 ADR 第 4 节）；实例的 REST 请求经接入网关 `/im/api/*`：只放行 SDK 登录同步与收发要用的路由，并校验请求体里的用户 ID 等于令牌的用户、查询对象属于本工作空间或本群（T-0059 补了好友申请、好友资料、群成员三个查询的校验）；改会话、搜消息、管理接口、拉别人的消息一律 404（场景 8）；管理员的 IM 全部经工作台 API 代理 |
| **V2：OpenIM 回调伪造** | 内部网络、随机路径、限定来源 | 回调只从 `im` 内部网络到达，地址里有随机口令（日志记成 `***`），只接受 openim-server 固定地址的请求；处理时只信任回调里的 ID，消息内容以 `pull_msg_by_seq` 拉回的为准；周期对账（30 秒增量、每天全量）兜底，丢失的回调也能补回（场景 12） |
| **V2：实例之间串通 / 群聊刷屏** | 禁止实例单聊；群聊防刷屏 | before 回调拒收实例之间的单聊（场景 11）；群里只有被 @ 的实例回复；实例连续发言达到上限（默认 8 条）自动暂停接力，并通知管理员，管理员发言后恢复；每个实例每分钟条数有上限（默认 6 条，场景 3）；可关闭“实例 @ 实例”的接力；实例只在被 @ 时回复（`requireMention`），只接受接入包生成时的管理员与本工作空间实例的消息（`inboundWhitelist`）；群里诱导一个实例去让另一个实例删除数据，没有审批不会执行（场景 10） |
| **V2：删好友、越权建群** | 接入网关拦截，回调后补回 | 接入网关拦截删好友、建群等接口（场景 5，`test_im_gateway.py`）；绕过网关删除好友后，after 回调触发自动补回并告警管理员（`im_friend_restored`） |
| **V2：外链图片（SSRF、追踪）** | 服务器端不抓取；浏览器端不自动加载 | 工作台不抓取消息里的外部图片地址；浏览器只显示“外部图片未加载”和地址文字；工作台自己的图片经 API 代理读取（上传者本人或能看到会话的管理员），实例经接入入口读取已发出的图片 |
| **V2：跨工作空间泄露** | 会话、消息、密钥、工具结果不跨工作空间 | 实例、凭证、密钥授权用 `(workspace_id, …)` 复合外键绑定同一工作空间；IM 会话按管理员与工作空间分开；工具调用记录只在所属工作空间可见（`test_records_stay_in_their_workspace`）；接入网关只允许访问本工作空间的 IM 账号 |
| **V2：供应链（OpenClaw、插件、OpenIM）** | 镜像锁定 digest；插件锁定版本；只允许一个插件 | OpenClaw 与 OpenIM 镜像按 tag + digest 锁定；`@openim/openclaw-channel` 锁定 0.3.3 并核对 tarball 的 sha256，CI 的 deps 任务重新下载核对并扫描（`tests/e2e/openclaw_plugin_review.py`），升级前按 `infra/openclaw/README.md` 重新审查；`plugins.allow` 只有 `openclaw-channel`；ci-security 的镜像扫描覆盖 OpenIM 的依赖镜像、agent-gateway、OpenClaw 与 ghostunnel（第三方只报告），OpenIM 补丁镜像由 ci-openim 扫描（有修复的严重漏洞就失败） |
| **V2：已知上游行为** | — | OpenIM 重启后 openclaw-channel 的 SDK 重新同步时会抛出未处理的错误，OpenClaw 进程退出、容器自动重启重连（约 45 秒），这段时间发给实例的消息插件不补处理（工作台的消息投影不缺）；实例间私聊被拒收时 `message` 工具仍显示 done。两者都不影响权限边界，记录在 Sprint 6 验收报告 |
