# 2026-11-03 OpenClaw 全自动部署：填写 SSH 服务器后自动安装并接入（T-0088 ~ T-0090）

- 状态：accepted（人类 2026-11-03 在对话中提出并选择方案；技术细节由 claude 决定）
- 关联：`docs/decisions/2026-10-31-v2-oc2-agent-ingress-verification.md`（接入入口）、
  `services/api/app/agent_bundle/`（接入包）、`services/api/app/domain/agent_ca.py`（内部 CA）、`docs/deploy-guide.md` 第 8.6 节

## 1. 需求与人类的选择

人类要求：「用户在 workspace 平台上填写新的 SSH 服务器 IP、端口、账号、密码后，平台能够自动去这台服务器安装 OpenClaw，
并且与 workspace 平台建立好连接。全流程做成自动化。」

| 问题 | 选择 |
|---|---|
| SSH 密码用完怎么办 | **只用一次**：只在安装时用，不写数据库、不进 Redis、不写日志；安装时生成一对专用 SSH 密钥，公钥装到那台服务器，私钥加密保存在工作台，以后升级、重装、卸载用它；可以随时吊销 |
| 防中间人 | **先显示指纹，确认后才安装**：第一次连接只读取主机公钥，页面显示 SHA256 指纹，owner 核对后确认；之后固定这个公钥，变了就拒绝连接 |
| 接入入口 | **一键脚本自动配置**：`deploy.sh connect` 新增一步，用工作台内部 CA 签发入口证书、写 `.env`、启动入口 |
| 没有 Docker | **自动安装**：Ubuntu 22.04 / 24.04、Debian 12，从 Docker 官方软件源安装锁定版本 |

## 2. 分三步实现

| 任务 | 内容 |
|---|---|
| T-0088（A） | 接入入口自动配置：内部 CA 签发服务器证书；`deploy.sh connect` 的“OpenClaw 接入入口”一步；接入包带上 CA 与地址映射 |
| T-0089（B） | SSH 自动安装的后端：指纹探测、一次性密码阶段、专用密钥、Docker 安装、部署接入包、等待上线；表结构、接口、审计 |
| T-0090（C） | 网页：实例页的“通过 SSH 自动部署”表单、指纹确认、安装进度；教程更新 |

## 3. A：接入入口自动配置（T-0088）

### 3.1 用外网模式

自动部署的 OpenClaw 服务器默认用**外网模式**（经公网访问工作台，实例令牌 + 客户端证书）：
内网模式要求两台机器在同一私网，入口只接受私网来源，不适合“随便一台 VPS”。

### 3.2 入口主机名与证书

- 工作台已经配置了域名（`SITE_DOMAIN`）：入口主机名就用这个域名，端口 9443（管理员入口在 443，互不影响），
  DNS 已经指向这台服务器，不需要新增记录；
- 没有域名（只用 IP 部署）：入口主机名用固定的内部名字 `agent-gateway.myworkspace.internal`（不会被公网解析），
  接入包在本机隧道容器里把它映射到工作台的公网地址（`AGENT_PUBLIC_ADDRESS`，Compose `extra_hosts`）；
  隧道（ghostunnel）按主机名发送 SNI 并校验证书，与外网入口“SNI 必须与 Host 一致”的要求相符。
- 入口的服务器证书由**工作台内部 CA** 签发（EC P-256，有效期 2 年，`serverAuth`，SAN 是入口主机名），
  不需要购买证书、也不需要 80 端口验证；内部 CA 本来就用于给外网模式的实例签发客户端证书。
- 用内部 CA 时 `AGENT_PUBLIC_INTERNAL_CA=true`：接入包附带 `tls/ca.pem`（只有证书，没有私钥），
  隧道命令加 `--cacert=/tls/ca.pem`，OpenClaw 服务器上不需要再手动配置私有 CA。

### 3.3 `deploy.sh connect` 的新步骤

1. 询问是否让其他服务器上的 OpenClaw 连回来（无人值守时跳过）；
2. 确定主机名（见 3.2）；没有域名时询问 OpenClaw 服务器访问这台服务器用的地址（默认取本机出站地址，可以改）；
3. 在 api 容器里调用 `python -m app.commands.agent_ca issue-server`，把证书与私钥写进 `AGENT_TLS_DIR`
   （私钥 600、属主 root；目录不存在时新建为 755：agent-gateway 以 root 运行但去掉了全部 capability，只能读属主是自己的 600 文件、进入别人可进入的目录；
   私钥只经过本机管道，不进日志、不存数据库）；没有 `intranet.crt` / `intranet.key` 时同时签一张内网入口的占位证书
   （主机名 `AGENT_INTRANET_HOST`，默认 `intranet.invalid`；内网入口默认只监听 127.0.0.1），否则同一个 Caddy 起不来；
4. 写 `.env`：`AGENT_PUBLIC_HOST`、`AGENT_PUBLIC_BIND=0.0.0.0`、`AGENT_PUBLIC_PORT=9443`、`AGENT_PUBLIC_URL`、
   `AGENT_PUBLIC_ADDRESS`（只在没有域名时）、`AGENT_PUBLIC_INTERNAL_CA=true`；重启 api、worker；`make agents-up`；
5. 用内部 CA 证书检查入口能完成 TLS 握手；提醒（检测到 ufw 时询问是否）放行 9443 端口；
6. 已经配置过的：证书还有 60 天以上、主机名没变、是这套 CA 签发的就不重签（`check-server`）；
   地址（`AGENT_PUBLIC_ADDRESS`）变了不用重签证书，只重启 api、worker；
   已经手动配置了外网入口（`AGENT_PUBLIC_URL` 有值、`AGENT_PUBLIC_INTERNAL_CA` 不是 true）的保持不变。

### 3.4 校验入口的握手

脚本用内部 CA 证书、按入口主机名（SNI）连 `127.0.0.1:<端口>` 做 TLS 握手。外网入口要求客户端证书，
但 TLS 1.3 下客户端这边的握手在服务器校验客户端证书之前就完成了，所以不带客户端证书也能确认服务器证书正确；
完整的双向证书路径由 CI 用原样的接入包验证（3.5）。

### 3.5 测试

- 单元测试：`services/api/tests/test_agent_ingress_setup.py`（签发、重签判断、命令行、配置校验、接入包），
  `test_agents_api.py`（接口生成的接入包带 CA 与地址映射），`test_deploy_script.py`（用假的 docker / make 运行
  `connect_agent_ingress`：无域名、有域名、证书仍有效、地址变化、手动配置不覆盖、非法 IP）；
- CI（`ci-infra` 的 agents 任务最后两步）：真实运行 `connect_agent_ingress`（无域名 → 内部主机名 + 本机出站地址），
  然后 `tests/e2e/agent_auto_ingress_acceptance.py`：生成外网接入包，**不做任何修改**只启动其中的 `tunnel` 服务，
  经本机隧道取到密钥、心跳成功——证明 OpenClaw 服务器上不需要手动配置 CA 或 hosts。

## 4. B、C 的要点（实现时补充核验结论）

- SSH 库：asyncssh 2.24.1（2026-10-04 发布；许可证 EPL-2.0 或 GPL-2.0-or-later，作为未修改的库使用，与本项目的 Apache-2.0 兼容；
  唯一必需依赖是项目已有的 `cryptography`）。核验见 B 的 ADR 补充。
- Docker 官方软件源（2026-11-03 核验，`https://download.docker.com/linux/{ubuntu,debian}/dists/{noble,jammy,bookworm}/`）：
  签名公钥指纹 `9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88`；锁定 `docker-ce` / `docker-ce-cli` `5:29.9.1-1~<发行版>`
  （2026-10-16）、`containerd.io` `2.4.2-2~<发行版>`（2026-10-16）、`docker-compose-plugin` `5.6.0-1~<发行版>`（2026-10-02），
  amd64 与 arm64 都有。
- 一次性密码只在 API 进程内存里，用于：校验已确认的主机公钥 → 装专用公钥 → （需要时）用 sudo 安装 Docker 并把账号加入 docker 组；
  之后的步骤（上传接入包、安装插件、启动、定时任务、升级、卸载）都用专用密钥，不再需要 root。
- 只有 owner 可以使用（与实例接口一致）；每一步写审计日志，命令输出脱敏后保存为安装记录。

### 4.1 asyncssh 核验（T-0089 B1，2026-11-03）

来源：PyPI <https://pypi.org/project/asyncssh/2.24.1/>（JSON 元数据）、源码 <https://github.com/ronf/asyncssh>，
以及在锁定版本上的实测（进程内的 asyncssh 服务器，见 `services/api/tests/test_agent_ssh.py`）。

| 项目 | 结论 |
|---|---|
| 版本 | 2.24.1，2026-10-04 发布（当时 PyPI 上的最新版）；wheel sha256 `fc560b4f43be0f0c602d184783e5e3876f5d24d933a25359d86e5a50a5f46fe5`（与 `uv.lock` 一致） |
| 许可证 | `EPL-2.0 OR GPL-2.0-or-later`；作为未修改的第三方库通过 pip 安装使用，与本项目 Apache-2.0 兼容 |
| 依赖 | `cryptography>=48.0.1`、`typing_extensions>=4.0.0`，项目已有（50.0.2、4.16.0），锁文件只新增 asyncssh 一个包；可选依赖都不装 |
| 读取主机公钥 | `get_server_host_key(host, port, ...)` 只完成密钥交换并返回服务器出示的主机公钥（文档：“connect to an SSH server and return the server host key presented during the SSH handshake”），不登录；实测服务器端的认证回调没有被调用 |
| 指纹 | `SSHKey.get_fingerprint("sha256")` 返回 `SHA256:` 加 base64，与 `ssh-keygen -lf` 相同；`export_public_key()` 是 OpenSSH 公钥行 |
| 固定公钥 | `connect(..., known_hosts=([公钥], [], []))`：三个列表分别是信任的主机公钥、CA、吊销的公钥（`known_hosts.match_known_hosts` 的文档）。实测：同一把公钥能连上；换一把就抛 `HostKeyNotVerifiable`；密码错误抛 `PermissionDenied` |
| 运行环境 | 不给 `config` 时会读 `~/.ssh/config`（`connection.py` 第 8243 行）；工作台一律传 `config=[]`。B2 登录时同样显式给出 `client_keys`、关掉 ssh-agent，不用运行环境里的默认私钥 |
| 专用密钥 | `generate_private_key("ssh-ed25519")`，`export_public_key()` 写进对方的 `authorized_keys` |

### 4.2 B1 的接口

- `GET /api/v1/agents/instances/{id}/ssh`：目标服务器与指纹（没有时 `target: null`）；
- `PUT .../ssh`（`host`、`port`、`username`，更换时带 `version`）：检查地址 → 读取主机公钥 → 保存，确认作废；
  只支持外网模式、未停用的实例；每个用户每分钟最多探测 10 次；
- `POST .../ssh/confirm`（`version`、`fingerprint`）：指纹必须与保存的一致；
- `DELETE .../ssh?version=`：已经装了专用公钥时要先吊销（B3）。
- 地址检查：拒绝回环（测试与 CI 用 `AGENT_SSH_ALLOW_LOOPBACK=true` 打开）、链路本地（含 169.254.169.254）、组播、
  未指定与保留地址；私网地址允许。域名只解析一次，之后都连解析出的地址（`address`）。
