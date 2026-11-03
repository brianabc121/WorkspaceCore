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

## 5. B：安装任务（T-0089 B2）

### 5.1 任务与密码

- `POST /api/v1/agents/instances/{id}/ssh/install`（`version`、`password`）：主机公钥已确认、外网入口已配置（`AGENT_PUBLIC_URL`）、
  实例启用且是外网模式、没有进行中的任务时，新建一条安装任务（`agent_ssh_jobs`，迁移 0022），立即返回 202；
  已经装了专用公钥、且 Docker 可以直接使用时（重新部署）可以不填密码，整个任务只用专用密钥；
- 任务在 **API 进程内** 的后台协程里运行（API 只有一个进程，`app/domain/agent_ssh_install.py` 的 `SshJobRunner`）：
  密码只作为这个协程的参数存在（`SecretStr`，装在一次性的容器对象里），不写数据库、不进 Redis、不写日志；
  密码阶段（登录、装公钥、装 Docker）一结束就清掉并断开密码连接；
- 密码只经标准输入交给 `sudo -S -p '' -k`，不出现在命令行里；命令里的路径都经过 `shlex.quote`；
- 每一步的状态写进 `steps`（JSON），命令输出的末尾一段（最多 64 KB）脱敏后写进 `log`：密码、实例令牌、Gateway 令牌、
  私钥（PEM）、`mwa_` 开头的令牌都替换成 `***`；每次写库用新的短会话；
- API 启动时，还在 `running` 的任务标为 `interrupted`（协程已经不在了），停止时取消进行中的任务并标为中断，owner 可以重新开始；
  启动时数据库暂时连不上（或还没迁移）只记警告、不阻止启动（最多等 5 秒，`/readyz` 照常报告）；
  记录是 `running` 但本进程里没有它的协程时，下一次操作把它标为中断（提交前先登记，不会误判刚提交、还没启动的任务）；
  同一台目标同时只能有一个任务（部分唯一索引）；任务进行中不能更换、确认或删除目标服务器（409 `AGENT_SSH_JOB_RUNNING`）；
- 专用私钥加密保存在 `agent_ssh_targets.client_key_ref`（用途 `agent_ssh:<目标 id>`），已加入加密密钥轮换；
- 审计：`agents.ssh.install_started` / `key_authorized` / `install_succeeded` / `install_failed` / `install_interrupted`，
  以及签发接入包的 `agents.bundle.issued`。

### 5.2 步骤

| 步骤 | 做什么 | 用什么登录 |
|---|---|---|
| connect | 校验固定的主机公钥（不一致时 `host_key_changed`，不登录），密码登录（也回答单个 Password: 的 keyboard-interactive） | 密码（重新部署时专用密钥） |
| inspect | `sh -s` 读入 `inspect.sh`：发行版、架构、uid / gid、主目录、是否已有 Docker / Compose、当前用户能否直接用 Docker、sudo、crontab | 同上 |
| authorize_key | 生成 ed25519 专用密钥（重新部署时沿用），公钥追加到 `~/.ssh/authorized_keys`（已有同一行就不重复，700 / 600），私钥加密保存 | 密码 |
| docker | 需要时用 root（root 直接运行，否则先 `sudo -S -p '' -k true` 检查）：上传 `install-docker.sh` 到 `~/.myworkspace-install/`（700），从 Docker 官方软件源安装锁定版本（核对签名公钥指纹）、把账号加入 docker 组，用完删除脚本；已经能用就跳过 | 密码 |
| reconnect | 断开密码连接，改用专用密钥重新登录（新会话里 docker 组生效），确认 `docker info` 与 `docker compose version` 可用 | 专用密钥 |
| upload | 生成外网接入包（签发新凭证与客户端证书，旧凭证再保留 24 小时），`~/myworkspace-openclaw/`（700）；重新部署且账号既不是 root 也不是 uid 1000 时，先用锁定镜像里的 chown 把 `state/`、`bin/`、`tls/` 收回给登录账号；SFTP 上传，权限与 zip 里一致 | 专用密钥 |
| pull | `docker compose pull --quiet` | 专用密钥 |
| plugin | `state/`、`bin/`、`tls/` 交给 uid 1000（root 用 chown；uid 1000 不用改；其他账号用锁定镜像里的 chown，`--network none --user 0`）；`docker compose run --rm --no-deps -T --entrypoint sh openclaw -s` 读入 `install-plugin.sh`：`npm pack --ignore-scripts` 锁定版本、核对 sha256、用临时的空配置安装（与 CHECKLIST 相同） | 专用密钥 |
| start | `docker compose up -d` | 专用密钥 |
| schedule | 当前用户的 crontab：每 10 分钟 `secrets reload`、每周一重启（行尾带 `# myworkspace-openclaw` 标记，重装时先删掉带标记的行）；没有 crontab 时跳过并提示手动配置 | 专用密钥 |
| online | 等这次签发的实例凭证第一次被使用（`last_used_at`，即新部署的 OpenClaw 连上了工作台），最多 15 分钟；超时时把 `docker compose ps` 与最后 60 行日志（脱敏）写进任务日志 | — |

成功后记下 `install_dir`、`installed_at`。

### 5.3 Docker 安装脚本

只支持 Ubuntu 22.04 / 24.04、Debian 12（amd64、arm64）。2026-11-03 在官方软件源的 `Packages` 索引里核对了 24 个锁定版本全部存在，
格式是 `<版本>~<ID>.<VERSION_ID>~<代号>`：`docker-ce` / `docker-ce-cli` `5:29.9.1-1~…`、`containerd.io` `2.4.2-2~…`、
`docker-compose-plugin` `5.6.0-1~…`；Ubuntu 与 Debian 的签名公钥指纹都是 `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`。
已经有 Docker 的服务器不检查发行版，只启动服务、加入 docker 组；没有 systemd 时最多等 60 秒守护进程就绪。
其他发行版且没有 Docker 时任务失败（`unsupported_os`），提示先手动安装 Docker 与 Compose 插件。

实际验证（2026-11-03，本会话自己的 Docker）：在 `ubuntu:24.04`、`ubuntu:22.04` 容器里以 root 运行脚本，
核对指纹、装上四个锁定版本并 `apt-mark hold`（容器里没有 init，守护进程不会启动，所以最后一步按预期失败）；
`debian:12` 在这个环境里拉不到 Debian 自己的软件源（代理拦截 deb.debian.org），只核对了 Docker 软件源的索引。

### 5.4 插件安装脚本

`install-plugin.sh` 在锁定的 OpenClaw 镜像（`openclaw/openclaw:2026.9.36@sha256:eefba69e…`，工作目录 `/app`，用户 node）里运行。
2026-11-03 在本会话的 Docker 里实际跑通：`npm pack` 下载 `@openim/openclaw-channel@0.3.3`、sha256 与锁定值一致、
`plugins install <tarball> --force --accept-capabilities` 输出 `Installed plugin: openclaw-channel`（与 OC-7 验收脚本的安装方式相同）。

### 5.5 测试

`tests/test_agent_ssh_install.py`：进程内的 asyncssh 服务器（密码与公钥登录、SFTP）把命令交给本机 `/bin/sh` 在临时目录里真正执行，
PATH 里是假的 docker、sudo、bash、chown、crontab；覆盖完整安装、重新部署（不用密码）、root 与 uid 1000 账号、
密码错误、sudo 被拒、不支持的发行版、插件校验失败、没有上线、主机公钥被换、前提条件、进行中的限制与重启中断，
并检查密码与令牌不出现在任务、目标服务器与审计记录里。真实 sshd + 真实 OpenClaw 的端到端验收在 B3。

## 6. B：卸载、吊销与端到端验收（T-0089 B3）

### 6.1 卸载

`POST /api/v1/agents/instances/{id}/ssh/uninstall`（`version`、`remove_files`）：任务（`kind = 'uninstall'`，迁移 0023 放宽检查约束），
只用专用密钥，步骤 connect → inspect → stop → unschedule → remove_files：

- stop：接入包目录里有 `compose.yaml` 时 `docker compose down --remove-orphans`；
- unschedule：删掉 crontab 里带 `# myworkspace-openclaw` 标记的行，其他行保留；
- remove_files（可选，不可恢复）：账号既不是 root 也不是 uid 1000 时先用锁定镜像里的 chown 收回属主，再
  `rm -rf -- <主目录>/myworkspace-openclaw`（只接受这一种路径）；
- 不卸载 Docker，不吊销专用公钥与实例凭证（实例页可以吊销凭证）；成功后清空 `installed_at`，删了目录时清空 `install_dir`。

### 6.2 吊销专用密钥

`POST /api/v1/agents/instances/{id}/ssh/revoke`（`version`、`force`）：锁住目标服务器这一行，用专用密钥登录，
从 `~/.ssh/authorized_keys` 删掉这把公钥所在的行（awk 按字段比较相邻的“类型 公钥数据”，制表符、多个空格、行首选项都能识别；先写临时文件再替换，其他行原样保留，删后按同样规则再核对一遍），
再删除工作台保存的私钥（审计 `agents.ssh.key_revoked`，`remote_removed`）。连不上时 503 `AGENT_SSH_UNREACHABLE`；
`force=true` 只删除工作台保存的私钥，返回的 `warning` 给出要在服务器上手动删除的那一行。吊销之后才能更换或删除目标服务器。

### 6.3 本机实测（2026-11-03）

本会话的沙箱里起了一个只监听 127.0.0.1:2222 的真实 sshd，账号在 sudo 组（要密码）、不在 docker 组：
密码登录（PAM）、inspect、装专用公钥、真实的 `sudo -S`、`install-docker.sh`（已有 Docker：只加入 docker 组）、
专用密钥重新登录（新会话里 docker 组生效）、SFTP 上传、`docker compose pull`、镜像里的 chown 交给 uid 1000 都通过；
插件一步因为沙箱的代理拦截 TLS 失败（`SELF_SIGNED_CERT_IN_CHAIN`）——由此发现 `npm pack --silent` 把错误也吞掉了，
改为 `--loglevel=error`，失败原因进任务日志。卸载（停止、收回属主、删除目录）与吊销（authorized_keys 清空）也在这台 sshd 上通过。

### 6.4 重新部署时不重装插件

CI 端到端第二次运行时发现：OpenClaw 运行过一段时间后，`state/` 里已有插件设置，带 `--force` 重装同一个插件会被拒绝
（`Plugin settings are not ready for activation: openclaw-channel. Run openclaw doctor --fix …`；第一次 CI 通过只是因为重装时
OpenClaw 还没走到这一步）。`install-plugin.sh` 先看 `extensions/openclaw-channel/package.json`：名字与版本都是锁定值时
输出 `plugin_already_installed` 并跳过（任务里这一步显示 skipped），也避免与正在运行的 OpenClaw 同时改 `state/`。
在锁定镜像里离线验证过两种情况（已安装 → 跳过、不联网；空的 state → 照常安装）。升级插件版本时的数据迁移
（`doctor --fix`）没有核验，留到升级插件时再做。

### 6.5 CI 端到端

`ci-infra` 的 agents job 在接入入口自动配置之后，把 runner 自己当成一台新服务器：另起一个 sshd（2222，监听
`AGENT_PUBLIC_ADDRESS`），账号 `mwdeploy` 要 sudo 密码、不在 docker 组；`tests/e2e/agent_ssh_acceptance.py`
走完指纹确认 → 密码安装（sudo 加入 docker 组）→ **真实的 OpenClaw 经外网入口连上工作台** → 密码不在数据库里 →
不填密码重装 → 卸载（删除目录）→ 吊销 → 删除目标服务器。

`docker-pins` job 在 `debian:12`、`ubuntu:22.04`、`ubuntu:24.04` 的干净容器里真正运行 `install-docker.sh`，
核对装上的是锁定版本并且都 hold 了（补上了本环境装不了的 Debian 12）。


## 7. C：网页（T-0090）

实例卡片上的「通过 SSH 自动部署」（`apps/web/src/features/agents/SshDeploy.vue`），只给外网模式的实例；内网实例只显示说明，不请求接口。

- 流程：填写服务器（IP 或域名、端口、账号，前端按后端同样的规则校验）→ `PUT /ssh` 读取主机公钥 → 显示算法、SHA256 指纹与核对命令
  （按算法给出 `ssh-keygen -lf /etc/ssh/ssh_host_<ed25519|ecdsa|rsa>_key.pub`，未知算法给出遍历全部主机公钥的命令）→
  勾选“已在服务器上核对”后才能 `POST /ssh/confirm` → 输入密码 `POST /ssh/install`；
- 密码：`type=password`、`autocomplete=off`，点开始部署时先从页面清空，再随这一个请求发出；不放进查询缓存、状态或浏览器存储；
  已经装了专用公钥时可以不填（重新部署）；
- 进度：`GET /ssh` 放在 `agents` 查询键下（写操作与 SSE 的 agents 主题都会刷新），任务 `running` 时每 2 秒轮询；
  任务结束时刷新实例列表（状态、凭证变了），失败时默认展开脱敏后的命令输出；
- 读取主机公钥与吊销要等对方服务器（连接超时 10 秒、登录 10 秒），用单独的 60 秒超时的客户端（`auth.slowApi`），
  默认的 15 秒会在服务器不响应时误报“结果未知”；结果未知时刷新状态；
- 吊销返回 503 `AGENT_SSH_UNREACHABLE` 或 409 `AGENT_SSH_KEY_INVALID` 时才出现“强制吊销”，确认后带 `force=true`，
  并显示返回的 `warning`（要手动删除的那一行公钥）；
- 装着专用公钥时不显示“更换 / 删除服务器”（后端也会拒绝），提示先吊销；
- 错误码在前端映射成说明（`AGENT_SSH_UNREACHABLE` 按 `details.reason` 区分 DNS、超时、拒绝、握手），429 显示等待秒数。
- 顺带把“第一次部署没填密码”的错误码从 `VALIDATION_ERROR` 改成全项目统一的 `VALIDATION_FAILED`（`docs/api-contract.md` 第 2 节）。

验收：`apps/web/tests/agents-ssh.test.ts`（6 个：完整流程且密码只出现在一个请求里、失败与轮询、免密码重新部署、卸载与强制吊销、
探测失败与限流、内网实例）；`tests/e2e/agent_ssh_browser.cjs` 在 CI 的 agents job 里接在接口级 SSH 验收之后，用同一台 sshd
全程在网页上操作到真实 OpenClaw 上线，再卸载、吊销、删除服务器，并检查 390px 宽度、密码不在页面 / 响应 / 浏览器存储里。
本机（真实 sshd、真实 API）跑通到插件一步（沙箱代理拦截 npm，`SSH_BROWSER_ALLOW_PLUGIN_FAILURE=1`），卸载与吊销通过。
