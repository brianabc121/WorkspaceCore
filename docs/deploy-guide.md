# 部署教程：从一台新服务器到能用的工作台

这份教程按顺序带你在**一台新的 Linux 服务器**上部署 My Workspace：准备服务器 → 启动 → 配置 HTTPS 域名 → 创建管理员 →
按需接入可选组件 → 配置备份 → 以后怎么升级。每一步都给出要执行的命令和怎么确认成功。

需要更多细节时看运行手册 [`deployment.md`](deployment.md)（每个变量、每个组件的完整说明）；
本教程里写“手册第 N 节”的地方都指它。

---

## 一键脚本（推荐）

在一台新的 Debian / Ubuntu 服务器上运行这一条命令。以 root 登录时（提示符是 `root@…#`）：

```bash
curl -fsSL https://raw.githubusercontent.com/brianabc121/WorkspaceCore/main/scripts/deploy.sh | bash
```

用有 sudo 权限的普通用户时：

```bash
curl -fsSL https://raw.githubusercontent.com/brianabc121/WorkspaceCore/main/scripts/deploy.sh | sudo bash
```

> Debian 最小安装通常**没有 sudo**：root 用户运行带 `sudo` 的命令会报 `sudo: command not found`，
> 管道里的 `curl` 随之报 `curl: (23) Failure writing output to destination`。以 root 登录时把 `sudo` 去掉即可。

> 有的最小安装镜像**连 curl 也没有**，会报 `curl: command not found`。先装上 curl 再运行上面的命令
> （普通用户在两个 `apt-get` 前加 `sudo`）；git、make、Python 3.12、Docker 之后都由脚本安装：
>
> ```bash
> apt-get update && apt-get install -y curl ca-certificates
> ```

脚本会把代码放到 `/srv/my-workspace`，然后显示菜单：

| 选项 | 做什么 |
|---|---|
| 1 部署 | 首次安装；已经装过时重新检查并修复（不会丢数据） |
| 2 升级 | 先加密备份数据库（`make backup`）与 `.env`，再拉取新代码、补 `.env` 新变量、重建、迁移、检查 |
| 3 清空 | 删除本项目全部容器、网络与数据卷（包括 n8n、OpenIM 的，以及以前部署过的 Vikunja 的），可选先备份；可选同时删除代码目录（其中的本机备份 `backups/` 与 `.env` 先移到 `/var/backups/my-workspace-<时间>/` 保留）；要输入项目名确认 |
| 4 状态 | 服务状态、本机与 HTTPS 健康检查、是否已完成首次安装 |
| 5 接入组件 | 自动接入 n8n，部署 OpenIM，配置 OpenClaw 接入入口（需要先在浏览器创建管理员，见下面的「自动接入 n8n、OpenIM」）；GitHub 在网页上接入（第 8.2 节） |

> **SSH 断开不会中断部署**（T-0092）：首次部署要下载几 GB 镜像、在服务器上编译 OpenIM，可能要几十分钟，
> 网络不稳时 SSH 会断开（例如 `Timeout, server not responding`）。在终端里选择部署、升级或接入组件时，
> 脚本自动进入一个专用的 tmux 会话（没有 tmux 时先自动安装），断线后部署在服务器上继续。重新登录后
> **再运行一次同样的命令**（或 `sudo tmux -L my-workspace attach -t deploy`；以 root 登录时去掉 `sudo`）就接回原来的进度，
> 不会开始第二次部署。万一没能自动接回（脚本会提示“部署在后台的会话里继续运行”），部署并没有中断，用上面的命令查看进度。
>
> - 想暂时离开但不中断：先按 `Ctrl+b`，再按 `d`；向上翻看输出：先按 `Ctrl+b` 再按 `[`，按 `q` 退出；
> - 脚本结束后停在最后的输出上（例如安装凭据），按回车才关闭会话；
> - 拉取镜像失败（网络超时）时自动重试 3 次，已经下载的部分不会重新下载；Docker Hub 的匿名拉取次数限制
>   （`429 toomanyrequests`）不重试，等一小时或 `docker login` 后重新运行；
> - 不想用 tmux：运行前设置 `WORKSPACE_NO_TMUX=1`（例如已经在自己的 screen 里）。

部署时只问三个问题：

| 参数 | 例子 | 说明 |
|---|---|---|
| `SITE_DOMAIN` | `workspace.example.com` | 已解析到这台服务器的域名 |
| `ACME_EMAIL` | `you@example.com` | Let's Encrypt 证书联系邮箱 |
| `PUBLIC_BASE_URL` | `https://workspace.example.com` | 直接回车就是 `https://<域名>`；必须和浏览器地址栏完全一致 |

其余的都自动完成，并处理了部署时常见的坑：

- 缺 git / make / curl 时用 apt 安装；没有 Docker 时用官方脚本安装（都会先问）。
  官方脚本在已停止支持的系统上可能失败（例如 Ubuntu 20.04：Docker 仓库最高只到 28.1.1，没有脚本要加装的 `docker-model-plugin`），
  这时改为只安装核心组件（docker-ce、CLI、containerd、Compose 与 Buildx 插件）；
- Ubuntu 22.04 以下、Debian 12 以下的系统已停止常规安全更新：照样部署，但会提醒生产环境换成 Ubuntu 24.04 或 Debian 12 及以上；
- 没有 Python 3.12 时安装官方独立构建（python-build-standalone 3.12.16，校验 SHA-256）到 `/opt/my-workspace/`，命令是 `python3.12`。
  项目脚本（生成 `.env`、备份）都用它，与 CI、api 镜像同一个版本；**系统自带的 `python3` 不替换**（apt 等系统工具依赖它），不需要为此升级系统；
- `.env` 不存在就随机生成；是从 `.env.example` 复制来的（还有占位符）时，备份后重新生成；
  重新生成时如果找到之前留下的 `.env` 副本，沿用其中的 `BACKUP_PASSWORD`（之前的备份用它加密，换了密码就打不开）；
- 写入 `APP_ENV=production`、`COMPOSE_FILE=compose.yaml:compose.https.yaml`、域名、邮箱与 `PUBLIC_BASE_URL`；
- 检查域名解析、80 / 443 是否被占用（例如 nginx）、ufw 是否放行；
- `make up` → `make migrate`（数据库密码和 `.env` 不一致时自动同步）→ `make smoke` → 用 GET 检查 `https://<域名>/healthz`；
- 还没创建管理员时显示安装凭据；在浏览器创建好管理员后回到终端按回车，接着自动接入 n8n、部署 OpenIM（见下一节）。GitHub 在网页上接入（第 8.2 节）。

### 自动接入 n8n、OpenIM

部署结束时脚本提示去浏览器「首次安装」页创建管理员；创建好后回到终端按回车，逐个询问要不要接入（都可以跳过）。
以后随时可以单独运行（菜单第 5 项）：

```bash
sudo bash scripts/deploy.sh connect
```

| 组件 | 脚本自动做的事 | 需要你做的 |
|---|---|---|
| n8n（自动化） | `make n8n-up` → 建 owner 账号（随机密码）→ 建只读 API Key 与名为 `X-Workspace-Trigger` 的 Header Auth 凭据 → 在工作台建连接并测试 | 脚本显示一次 n8n 账号密码，同时加密保存到工作台「自动化」页（只有管理员看得到）；之后自己搭工作流（Webhook 节点选这个凭据），再到工作台“自动化”页登记 |
| OpenIM（「消息」页） | 构建打过补丁的 openim-server 镜像（第一次约 5 ~ 15 分钟，临时占用约 8 GB 磁盘；Docker Hub 限流时自动改用 `mirror.gcr.io` 再试）→ `make openim-up` → 在 `.env` 里填好 `OPENIM_API_URL`、`OPENIM_OBJECT_URL` 并重启 api、worker → 确认工作台连得上 | 内存少于 4 GB 时默认不装（另外占用约 1 GB）；OpenClaw 本身（接入入口与实例）按第 8.6 节配置 |

- 项目与任务由工作台自己管理，不需要接入（T-0082 移除了 Vikunja；从带 Vikunja 的旧版本升级见第 10 步）。
- 接入以首次安装创建的管理员身份进行：Token 加密保存在工作台，操作写进审计日志，和在网页上配置一样。
- 密码和 Token 不出现在命令行参数和日志里。
- 已经接入的组件不会重复配置。
- **GitHub 不在脚本里接入**：在工作台网页上配置（第 8.2 节），脚本只显示它的状态和设置页的地址。
- n8n owner 已经存在时，脚本改为询问它的密码。
- 登录成功的 n8n 账号密码加密保存到工作台：管理员在「自动化」页的「登录账号」卡片里点「显示密码」
  或「复制密码」（每次都写审计日志），也可以在那里修改。之前已经接入、还没保存的，再运行 `connect` 时会问一次，
  输入账号密码（脚本先登录核对）后保存；也可以直接在页面上填写。member、viewer 看不到这张卡片。
- 无人值守（`--yes`）时自动接入 n8n，跳过 GitHub。
- 最后显示接入状态：「已接入」「未接入」，或者已保存但用不了的原因（凭证失效、暂时连不上等），以及 n8n 的打开地址。
- 依据和实测记录见 [`decisions/2026-11-02-deploy-connect.md`](decisions/2026-11-02-deploy-connect.md)。

#### 用子域名打开 n8n（可选）

默认 n8n 的管理界面只绑定服务器本机，工作台里「打开 n8n 编辑器」指向 `http://localhost:5678`，
要先建 SSH 隧道（第 8 节开头）才能打开。启用了域名与 HTTPS 时，`connect` 最后会问要不要用子域名对外打开：

1. 先在域名服务商那里加一条 A 记录，指向这台服务器，例如 `n8n.workspace.example.com`；
2. 运行 `sudo bash scripts/deploy.sh connect`，问到「用子域名经 HTTPS 对外打开 n8n？」时回答 `y`，
   域名直接回车用建议值（`n8n.<SITE_DOMAIN>`）或输入自己的。

脚本会：在 `infra/caddy/apps-enabled/` 生成 Caddy 站点（自动申请证书，转发到容器）；把 `.env` 的 `N8N_DOMAIN`、
`N8N_PUBLIC_URL` 改成新地址，同时设 `N8N_SECURE_COOKIE=true`、`N8N_PROXY_HOPS=1`；
重启 n8n 与 Caddy（先校验配置，不通过就撤销，工作台不受影响）；更新工作台里的打开链接；最后检查 HTTPS。

- 公开后 n8n 的登录页暴露在公网。n8n 的 owner 能编辑和运行工作流，权限很大：用脚本生成的随机密码或同样强的密码，
  在 n8n 设置里开启两步验证；不需要时再运行 `connect`，在域名提示处输入 `-` 取消对外。
- 已经公开的，再运行 `connect` 时回车保持原样；无人值守（`--yes`）时沿用 `.env` 里的设置。

已经有代码时，在代码目录里直接运行，也可以直接指定动作：

```bash
sudo bash scripts/deploy.sh upgrade
```

无人值守（例如自动化）时用参数代替提问：

```bash
sudo bash scripts/deploy.sh install --domain workspace.example.com --email you@example.com --yes
```

清空在无人值守时还必须加 `--confirm my-workspace`；连代码目录一起删除再加 `--delete-dir`。全部选项见 `sudo bash scripts/deploy.sh --help`。

脚本部署工作台本身，并可以自动接入 n8n、部署 OpenIM（上一节）；GitHub 在网页上接入（第 8.2 节）；监控、OpenClaw（AI 运维助手，第 8.6 节）等其他可选组件以及异地备份，仍按第 8、9 步配置。
脚本跑完后，下面第 1 ~ 7 步都已完成。想了解每一步做了什么、或者手动部署，就按下面的步骤来。

---

## 0. 先选部署形态

| 形态 | 包含 | 适合 |
|---|---|---|
| **最小** | 工作台本身（Caddy、前端、API、Worker、PostgreSQL、Redis） | 先试用：首页、项目与任务、日历、通知、收藏、成员管理 |
| **常用** | 最小 + GitHub（只读）+ 监控（Prometheus / Grafana） | 日常使用 |
| **完整** | 常用 + n8n（自动化）+ OpenIM 与 OpenClaw（V2：管理员与 AI 实例的 IM、写操作审批） | 需要 AI 运维助手 |

可选组件都可以以后再加，不影响已有数据。建议先按“最小”跑通第 1 ~ 7 步，再到第 8 步逐个添加。

---

## 1. 准备服务器

| 项目 | 建议 |
|---|---|
| 系统 | 64 位 Linux（下面的命令以 Debian 12 / Ubuntu 24.04 为例） |
| CPU / 内存 | 最小形态 2 核 / 2 GB；常用形态 4 GB；完整形态（含 OpenIM）4 GB 起，8 GB 更宽裕 |
| 磁盘 | 20 GB 起；要部署 OpenIM 时 40 GB 起（构建补丁镜像要约 8 GB 临时空间） |
| 网络 | 能访问 Docker Hub 与 GitHub；要用 HTTPS 域名时，80 与 443 端口能从公网访问 |
| 域名（推荐） | 一个解析到这台服务器的域名，例如 `workspace.example.com` |

本机实测的空闲内存（供参考）：工作台 6 个容器合计约 330 MiB（API 约 125 MiB、Worker 约 90 MiB、PostgreSQL 约 80 MiB）；
OpenIM 一套约 1 GiB（手册第 18.1 节）。

> 不建议直接用 root 日常操作。下面的命令用一个有 sudo 权限的普通用户执行，代码放在 `/srv/my-workspace`。
> 如果你以 root 登录、系统又没有 sudo（Debian 最小安装常见，会报 `sudo: command not found`），把命令里的 `sudo` 去掉即可。

---

## 2. 安装 Docker 与工具

需要：Docker（含 Compose v2 插件）、GNU Make、Git、Python 3。Python 只用来运行仓库脚本（生成 `.env`、备份）：
`make` 优先用 `python3.12`（与 CI、api 镜像一致；一键脚本会自动安装），没有时用系统的 `python3`（3.8 及以上即可）。

```bash
sudo apt-get update && sudo apt-get install -y git make python3 curl
```

```bash
curl -fsSL https://get.docker.com | sudo sh
```

```bash
sudo usermod -aG docker "$USER"
```

执行完 `usermod` 后**重新登录一次**，让 docker 组生效。确认版本：

```bash
docker version && docker compose version && make --version | head -1 && (python3.12 --version || python3 --version)
```

`docker compose version` 要能输出 v2.x；如果提示没有 `compose` 子命令，说明缺少 Compose 插件，按 Docker 官方文档为你的发行版安装 `docker-compose-plugin`。

---

## 3. 获取代码

```bash
sudo mkdir -p /srv/my-workspace && sudo chown "$USER": /srv/my-workspace
```

```bash
git clone https://github.com/brianabc121/WorkspaceCore.git /srv/my-workspace
```

```bash
cd /srv/my-workspace
```

后面所有命令都在 `/srv/my-workspace` 目录下执行。`make help` 可以列出全部命令。

---

## 4. 生成并检查 `.env`

```bash
make env
```

它从 `.env.example` 生成 `.env`，并把数据库密码、会话密钥、加密密钥、安装凭据、备份密码等**随机生成**。
`.env` 已存在时不会改动已有的值（升级后再运行，只会补上新增的变量）。

> **不要用 `cp .env.example .env`**：复制出来的文件里全是占位符（例如 `CREDENTIAL_ENCRYPTION_KEY=GENERATE_32_BYTE_KEY`、
> 数据库密码 `CHANGE_ME`），api 会拒绝启动。`make env`（`make up` 也会先运行它）每次都会检查 `.env`：
> 还有占位符、加密密钥不是 64 位十六进制、生产环境的 `SESSION_SECRET` 不到 32 个字符、用了 Windows 换行时，
> 它会列出问题并停下，不会开始构建。处理方法见第 12 节“常见问题”。

用编辑器打开 `.env`，**生产环境**至少确认这几项：

| 变量 | 改成 | 说明 |
|---|---|---|
| `APP_ENV` | `production` | 关闭 API 文档页；会话 Cookie 加 `Secure` |
| `PUBLIC_BASE_URL` | `https://workspace.example.com` | 浏览器访问工作台的地址，**必须和实际地址完全一致**，否则登录会被拒绝（`403 CSRF_FAILED`） |
| `HTTP_BIND` / `HTTP_PORT` | 保持 `127.0.0.1` / `8080` | 用 HTTPS 域名访问时，8080 只给本机检查用，不要对外 |

然后**马上把 `.env` 备份到服务器以外的地方**（例如密码管理器）。它里面有：

- `CREDENTIAL_ENCRYPTION_KEY`：加密已保存的第三方凭证（GitHub Token、n8n API Key 等），**丢了就解不开**；
- `BACKUP_PASSWORD`：备份仓库的密码，**丢了备份就打不开**；
- `SESSION_SECRET`、`SETUP_TOKEN` 等。

`.env` 不要提交到 Git（已在 `.gitignore` 里）。

---

## 5. 配置域名与 HTTPS（推荐）

工作台自带的入口 Caddy 可以自动向 Let's Encrypt 申请、续期证书。

**5.1 解析域名**：在域名服务商那里加一条 A 记录（IPv6 用 AAAA），把 `workspace.example.com` 指向服务器的公网 IP。
等几分钟后确认：

```bash
getent hosts workspace.example.com
```

**5.2 打开防火墙**：放行 80（申请证书、HTTP 跳转 HTTPS）和 443。以 ufw 为例（别忘了先放行 SSH）。
Debian 默认没有装 ufw（`ufw: command not found`），也没有启用别的防火墙，这时只要云控制台的安全组放行即可；
想用 ufw 就先 `sudo apt-get install -y ufw`，再执行下面的命令，**一定先放行 SSH 再 enable**，否则会把自己锁在外面：

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443 && sudo ufw enable
```

云服务器还要在控制台的安全组里放行 80 和 443。**不要**对公网开放 8080、3000、5678 这些端口。

**5.3 在 `.env` 里启用 HTTPS**：取消 `COMPOSE_FILE` 那一行的注释，填上域名和邮箱：

```dotenv
COMPOSE_FILE=compose.yaml:compose.https.yaml
SITE_DOMAIN=workspace.example.com
ACME_EMAIL=you@example.com
PUBLIC_BASE_URL=https://workspace.example.com
```

- `COMPOSE_FILE` 让之后的 `make up`、`docker compose …` 自动带上 `compose.https.yaml`：Caddy 改用 `infra/caddy/https.Caddyfile`，
  发布 80 / 443，HTTP 自动跳转 HTTPS，并加上 HSTS；
- `ACME_EMAIL` 是 Let's Encrypt 的联系邮箱（证书出问题时通知你）；
- 容器之间的内部地址 `http://caddy:8080` 保持不变，宿主机上的 8080 仍只绑定本机。

> **只在可信内网里使用、不配域名**：跳过第 5 步，把 `HTTP_BIND` 改为 `0.0.0.0`，`PUBLIC_BASE_URL` 改为 `http://<服务器内网 IP>:8080`。
> 这时流量没有加密，不要这样暴露到公网。

---

## 6. 启动、迁移、检查

```bash
make up
```

第一次会构建镜像（几分钟），然后等所有服务通过健康检查（最多 300 秒）。接着把数据库迁移到最新版本：

```bash
make migrate
```

检查：

```bash
make ps
```

```bash
make smoke
```

- `make ps`：6 个服务都应是 `Up (healthy)`；
- `make smoke`：经 Caddy 检查健康、就绪、前端页面与安全响应头，最后输出“冒烟检查全部通过”。

启用了 HTTPS 的，再从你自己的电脑上确认证书已经签好：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://workspace.example.com/healthz
```

应输出 `200`。如果报证书错误（例如 `SSL certificate problem`），看第 12 节“常见问题”。
不要用 `curl -I`：它发的是 HEAD 请求，`/healthz` 只接受 GET，会返回 `405`（这时证书其实已经正常）。

---

## 7. 首次安装：创建管理员

查看安装凭据：

```bash
make setup-token
```

浏览器打开 `PUBLIC_BASE_URL`（例如 `https://workspace.example.com`），会自动进入安装页。填写：

- 安装凭据（上面查到的那串）；
- 邮箱、显示名、密码（12 ~ 128 个字符）；
- 工作空间名称。

提交后你就是这个工作空间的 **owner**。首次安装只能成功一次，之后可以把 `.env` 里的 `SETUP_TOKEN` 清空，再 `make up`。

**添加家人或同事**：在“设置”页的“成员”里添加。

- 对方已经有账号：填邮箱，选择角色即可；
- 对方还没有账号：勾选“同时创建账号”，填显示名和初始密码，把初始密码私下告诉对方，并提醒对方登录后到“安全设置”里修改。

角色：owner 管理一切；admin 管理集成和成员（只能管理 member / viewer）；member 修改业务内容；viewer 只读。

到这里，最小形态已经可以使用了。

---

## 8. 按需接入可选组件

每个组件都可以单独加，顺序随意。改了 `.env` 之后都要再执行一次 `make up`，让工作台读到新的配置。

> 下面几个组件的管理界面默认只绑定服务器本机（`127.0.0.1`），不对外。从你自己的电脑访问时用 SSH 隧道最安全，例如：
> ```
> ssh -L 3000:127.0.0.1:3000 -L 5678:127.0.0.1:5678 <用户>@<服务器>
> ```
> 然后在本机浏览器打开 `http://localhost:3000`（Grafana）、`http://localhost:5678`（n8n）。

### 8.1 项目与任务（不需要接入）

项目与任务由工作台自己管理（2026-11-02 移除了 Vikunja，T-0082），不用部署任何组件；从带 Vikunja 的旧版本升级见第 10 步。

### 8.2 GitHub（只读）

在工作台网页上接入（管理员）：侧栏「管理 → 集成设置」→ **新建 GitHub 连接**，按弹窗里的三步做：

1. 点 **GitHub 生成 Token 页面** 链接：已经预填名称、90 天有效期，以及只读的 Issues、Pull requests、Actions 权限
   （Metadata 总是只读）。Repository access 选 **Only select repositories**，勾选要同步的仓库（最多 20 个），点 **Generate token**；
2. 把 `github_pat_` 开头的 Token 粘贴到“GitHub Token”，点 **读取可访问的仓库**：列出这个 Token 能访问的仓库并默认全选，
   去掉不要的；读不到时也可以在“GitHub 仓库列表”里手动填写（每行一个 `owner/repo`）；
3. 点 **保存 GitHub 连接**：页面自动检查权限（逐个仓库检查四项读权限），全部通过后立即开始第一次同步，
   之后在「开发者」页查看仓库、Issues、Pull requests 与 Actions。

- 以后换 Token 或改仓库：点连接卡片上的 **修改连接**；只改仓库时不用重新粘贴 Token（“读取可访问的仓库”会用已保存的 Token）。
- 不要用经典 Token（`ghp_` 开头），页面会拒绝。Token 只写不读：保存后页面上不会再显示。
- 服务器要能访问 `https://api.github.com`。细节见手册第 10 节。
- 一键脚本不再询问 GitHub（T-0091）；`deploy.sh connect` 只显示 GitHub 的状态和设置页的地址。

### 8.3 AFFiNE（文档收藏）

工作台不连接 AFFiNE，只保存你收藏的文档链接。自己部署 AFFiNE 后，在“知识库”里填写 AFFiNE 的外部地址（和 AFFiNE 的
`server.externalUrl` 完全一致），之后就能收藏文档链接。部署 AFFiNE 时要改掉官方 Compose 里不安全的默认配置，见手册第 9 节。

### 8.4 监控（Prometheus + Grafana）

在 `.env` 里填上（`GRAFANA_ADMIN_PASSWORD` 已由 `make env` 生成）：

```dotenv
PROMETHEUS_URL=http://prometheus:9090
GRAFANA_URL=http://grafana:3000
GRAFANA_DASHBOARD_URL=http://localhost:3000/d/workspace-overview/my-workspace
```

```bash
docker compose --profile monitoring up -d --wait
```

```bash
make up
```

“运维”页里 Prometheus、Grafana 显示“正常”。Grafana 用 `admin` 和 `.env` 里的 `GRAFANA_ADMIN_PASSWORD` 登录，
预置的仪表盘在 “My Workspace” 文件夹。细节见手册第 12 节。

### 8.5 n8n（自动化）

> 一键脚本可以自动完成第 1、2 步和第 3 步的连接（不含登记工作流）：`sudo bash scripts/deploy.sh connect`。

```bash
make n8n-up
```

1. 确认 `.env` 的 `INTEGRATION_ALLOWED_HOSTS` 里有 `n8n`（新生成的 `.env` 默认就是 `n8n`），然后 `make up`；
2. 打开 n8n 编辑器，创建 owner 账号；创建只读的 API Key（`workflow:list`、`workflow:read`、`execution:list`、`execution:read`）
   和名为 `X-Workspace-Trigger` 的 Header Auth 凭据；工作流用 Webhook 节点开头；
3. 工作台“自动化”页：填连接（`base_url` 为 `http://n8n:5678`），测试连接，登记白名单工作流。

`N8N_ENCRYPTION_KEY` 要和备份分开保存。完整步骤见手册第 12.4 节。

### 8.6 OpenClaw（AI 运维助手，V2，可选）

**OpenClaw 不装在工作台这台服务器上。** 它装在另一台服务器上（下面叫“OpenClaw 服务器”，工作台所在的叫“工作台服务器”），
用工作台发给它的凭证**主动连回工作台**：

- 管理员在工作台的「消息」页和它单聊、群聊（群里要 @ 它）；
- 它读工作台的数据（任务、日程、运维状态等）走工作台的接口；要**改**数据时（例如建任务），必须先在「AI 审批」页批准；
- 它调用模型用的 API Key 存在工作台的「密钥库」里，OpenClaw 服务器上不保存明文。

一共 5 步：① 工作台服务器部署 OpenIM（消息中转）→ ② 工作台服务器打开接入入口 → ③ 工作台网页登记实例、下载接入包 →
④ OpenClaw 服务器解压接入包并启动 → ⑤ 确认。一台工作台可以接多台 OpenClaw 服务器，每台重复第 ③ ~ ⑤ 步。

**先选网络模式**（登记实例时要选，登记后不能改）：

| | 内网模式 | 外网模式 |
|---|---|---|
| 什么时候用 | 两台服务器在同一个私网（同一机房、同一 VPC，或都装了 Tailscale） | OpenClaw 服务器只能经公网访问工作台 |
| 工作台服务器要准备 | 一个内网能解析的域名，以及它的证书（内部 CA 或 Tailscale 签发） | **一键脚本自动配置**（推荐，见第 ② 步）；或者自己准备公网域名与公网证书 |
| 安全措施 | 只接受私网来源；实例令牌 | 实例令牌 + 客户端证书（接入包里自带，90 天到期） |
| 防火墙 | 入口端口只对私网开放 | 入口端口对公网开放；OpenClaw 服务器只需出站 |

#### ① 部署 OpenIM（工作台服务器）

```bash
sudo bash scripts/deploy.sh connect
```

问到 OpenIM 时回答 `y`。脚本会构建打过补丁的 OpenIM（第一次 5 ~ 15 分钟，临时占用约 8 GB 磁盘，之后常驻约 1 GB 内存）、
启动它并把地址写进 `.env`。完成后工作台「运维」页里 OpenIM 显示“正常”；打开「消息」页，管理员的 IM 账号会自动注册。

<details><summary>手动部署 OpenIM</summary>

```bash
make openim-build
make openim-up
```

然后在 `.env` 里设置 `OPENIM_API_URL=http://openim-server:10002`、`OPENIM_OBJECT_URL=http://garage:3900`，再 `make up`。详见手册第 17 节。
</details>

#### ② 打开接入入口（工作台服务器）

**外网模式：一键配置（推荐）。** 再运行一次

```bash
sudo bash scripts/deploy.sh connect
```

问到「配置接入入口？」时回答 `y`，脚本会：

1. 确定入口的主机名：工作台配置了域名（`SITE_DOMAIN`）就用它，端口 9443（管理员页面仍在 443，DNS 不用改）；
   没有域名（只用 IP 部署）时回答 `-`，改用固定的内部名字 `agent-gateway.myworkspace.internal`，
   再填 OpenClaw 服务器访问这台服务器用的 IP（默认是本机的出站地址；云服务器上一般要改成**公网 IP**）；
2. 用工作台的**内部 CA** 签发入口证书（2 年，剩不到 60 天时再运行一次会自动重签），写进 `infra/caddy/agent-tls/`；
   不需要买证书，也不需要 80 端口验证；
3. 写 `.env`（`AGENT_PUBLIC_HOST`、`AGENT_PUBLIC_BIND=0.0.0.0`、`AGENT_PUBLIC_PORT`、`AGENT_PUBLIC_URL`、
   `AGENT_PUBLIC_ADDRESS`、`AGENT_PUBLIC_INTERNAL_CA=true`），重启 api、worker，`make agents-up` 启动入口，并检查入口能握手；
4. 检测到 ufw 时询问是否放行 TCP 9443。**云服务器的安全组也要放行 TCP 9443**（脚本改不了安全组）。

这样生成的外网接入包自带内部 CA 证书（`tls/ca.pem`）和地址映射，OpenClaw 服务器上不用再改任何配置。
已经手动配置过外网入口（下面的做法）的，脚本保持不变。

**手动配置**（内网模式，或者想用自己的公网证书）：

1. **放证书**：放进 `infra/caddy/agent-tls/`（目录已被 git 忽略，不会提交）：
   - 内网模式：`intranet.crt`、`intranet.key`；
   - 外网模式：`public.crt`、`public.key`（证书链放在 `.crt` 里）。
2. **改 `.env`**（只填你用的那种模式）：

   内网模式，例如工作台服务器的私网地址是 `10.0.0.5`、私网域名是 `ws.internal.example`：

   ```dotenv
   AGENT_INTRANET_HOST=ws.internal.example
   AGENT_INTRANET_BIND=10.0.0.5
   AGENT_INTRANET_PORT=8443
   AGENT_INTRANET_ALLOWED_CIDRS=10.0.0.0/8
   AGENT_INTRANET_URL=https://ws.internal.example:8443
   ```

   外网模式，例如公网域名是 `agent.example.com`：

   ```dotenv
   AGENT_PUBLIC_HOST=agent.example.com
   AGENT_PUBLIC_BIND=0.0.0.0
   AGENT_PUBLIC_PORT=9443
   AGENT_PUBLIC_URL=https://agent.example.com:9443
   ```

   `*_URL` 会写进接入包，没填的那种模式不能生成接入包。每个变量的说明见手册第 16.1 节。
3. **启动入口**：

   ```bash
   docker compose up -d api
   make agents-up
   ```

   `make agents-up` 会导出工作台的内部 CA 证书并启动入口 `agent-gateway`；之前已经部署了 OpenIM 的，会顺便让 OpenIM 接上入口。
4. **防火墙**：内网模式只对私网放行 `AGENT_INTRANET_PORT`；外网模式对公网放行 `AGENT_PUBLIC_PORT`。

#### ③ 登记实例、下载接入包（工作台网页，用 owner 账号）

1. 侧栏「管理 → 密钥库」：**添加密钥**，类型选“模型服务密钥”，值粘贴模型服务商的 API Key（只能写入、不能再读出）。
2. 侧栏「连接与工具 → OpenClaw 实例」：确认页面顶部的**总开关**是打开的（默认打开），点**登记实例**：
   - 名称、IM 昵称（在消息页里显示的名字）；
   - **网络模式**：内网或外网（登记后不能改）；
   - **工具权限**：“只读”，或“读写（写操作要审批）”——写操作每次都要在「AI 审批」页批准；
   - **最高角色**：它以什么角色访问工作台（实际取你和这里的较低者）。
3. 在这个实例的卡片上编辑**密钥授权**：选刚才的密钥，`ref_id` 填 `openai_api_key`。
   接入包里的配置默认用 OpenAI 和这个 id；用别的模型服务商时，这里的 `ref_id` 和第 ④ 步配置里的 id 要一致。
4. 点**生成并下载接入包**，得到 `myworkspace-agent-xxxxxxxx.zip`。**里面的凭证只出现这一次**，保存好、不要外传；
   泄露了就回到这里重新生成（旧凭证再保留 24 小时）或停用实例。

#### ④ 部署 OpenClaw（OpenClaw 服务器）

准备：一台 Linux 服务器，装好 Docker（含 Compose v2，安装方法同第 2 步）；能访问模型服务商；
能访问工作台的入口（内网模式经私网，外网模式经公网）；第一次安装插件时能访问 npm 仓库。

1. **上传并解压**，设置属主和权限（容器里的用户是 uid 1000，接入包里有凭证）：

   ```bash
   sudo mkdir -p /opt/myworkspace-agent
   cd /opt/myworkspace-agent
   sudo unzip ~/myworkspace-agent-xxxxxxxx.zip
   sudo chown -R 1000:1000 .
   sudo chmod 700 .
   sudo chmod 0755 bin/*
   sudo chmod 600 myworkspace.env state/openclaw.json
   ```

   外网模式还有 `tls/client.key`，也设成 600：`sudo chmod 600 tls/client.key`。

2. **安装 IM 插件**（第一次启动之前）。先核对插件包的 sha256 是
   `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`（有 npm 的机器上
   `npm pack @openim/openclaw-channel@0.3.3` 后对 `openim-openclaw-channel-0.3.3.tgz` 运行 `sha256sum`），然后：

   ```bash
   echo '{}' | sudo tee state/plugin-install.json >/dev/null
   sudo chown 1000:1000 state/plugin-install.json
   sudo docker compose run --rm --no-deps -e OPENCLAW_CONFIG_PATH=/home/node/.openclaw/plugin-install.json --entrypoint node openclaw openclaw.mjs plugins install @openim/openclaw-channel@0.3.3 --force --accept-capabilities
   ```

   用一份临时的空配置安装，是因为正式配置里已经写了插件提供的 `openim` 渠道，插件装好之前 OpenClaw 会拒绝那份配置。

3. **模型**：默认用 OpenAI，不用改。用别的服务商时，修改 `state/openclaw.json` 里 `models.providers` 那一段
   （写法见 OpenClaw 的文档），`apiKey` 保持 `{ "source": "exec", "provider": "myworkspace", "id": "<ref_id>" }` 的形式，
   `id` 与第 ③ 步的 `ref_id` 一致。**不要写明文 Key**，也不要加 `_comment` 之类的说明键（OpenClaw 会判定整个配置无效）。

4. **启动**：

   ```bash
   sudo docker compose up -d
   sudo docker compose logs -f openclaw
   ```

   冷启动要 2 ~ 4 分钟。外网模式会同时启动本机隧道 `tunnel`；内网模式、且工作台也配置了外网入口时会多一个 `image-proxy`。

5. **定时任务**（`sudo crontab -e`，把路径换成你的目录）：

   ```cron
   */10 * * * * cd /opt/myworkspace-agent && docker compose exec -T openclaw node openclaw.mjs secrets reload --json
   17 4 * * 1 cd /opt/myworkspace-agent && docker compose restart openclaw
   ```

   第一行：工作台里轮换了模型密钥，10 分钟内生效，不用重启；第二行：每周重启一次，换新的 IM 令牌（令牌只在启动时领取）。

入口证书是你自己的私有 CA 签发的（第 ② 步一键配置的不算，接入包已经带上了 CA）：外网模式在 `compose.yaml` 里 `tunnel` 的命令加 `--cacert=<CA 证书>`；
内网模式在 `myworkspace.env` 里设置 `NODE_EXTRA_CA_CERTS=<CA 证书>`，有 `image-proxy` 时它的命令也加 `--cacert=<CA 证书>`。

接入包里的 `CHECKLIST.md` 是这台服务器的完整清单（安装、加固、定时任务、备份），上线和每次改配置后都对照一遍。

#### ⑤ 确认

- 工作台「OpenClaw 实例」页：这个实例从“等待接入”变成“在线”；「运维」页的“OpenClaw 与 IM”一组正常；
- 「消息」页：通讯录里出现这个实例，私聊发一句话能收到回复（群聊里要 @ 它）；
- 让它做一件要改数据的事（例如“帮我建一个任务：明天检查备份”），「AI 审批」页出现待审批的调用，批准后才会执行。

#### 日常维护

- **重新生成接入包**会签发新凭证，旧凭证再保留 24 小时；外网模式的客户端证书 90 天到期，到期前重新生成并替换到 OpenClaw 服务器上；
- 要立刻断开某个实例：在实例页停用它；断开全部实例：关闭总开关。停用后它取不到密钥、调不了工作台接口、IM 被强制下线；
- 重启或升级 OpenIM 后，实例会自己重启一次（约 45 秒）再连上，这段时间发给它的消息需要重发；
- 工作台服务器上的完整说明见手册第 16 ~ 18 节（入口变量、验收脚本、两种网络模式的细节、已知的上游行为）。

---

## 9. 配置备份

`make backup` 用 restic 把工作台数据库（以及正在运行的 OpenIM）加密备份到 `BACKUP_REPOSITORY`。
默认仓库是本机目录 `./backups/restic`，**只适合测试**：服务器坏了，备份也一起没了。生产请用异地仓库。

**9.1 用 S3 兼容的对象存储**（例如云厂商的对象存储）。在 `.env` 里：

```dotenv
BACKUP_REPOSITORY=s3:https://s3.example.com/my-bucket/workspace
```

S3 的凭据**不写进 `.env`**，放进一个只有自己能读的文件：

```bash
sudo install -d -m 700 /etc/my-workspace
```

```bash
sudo tee /etc/my-workspace/backup.env > /dev/null <<'EOF'
AWS_ACCESS_KEY_ID=<访问密钥 ID>
AWS_SECRET_ACCESS_KEY=<访问密钥>
AWS_DEFAULT_REGION=<区域>
EOF
```

```bash
sudo chown "$USER": /etc/my-workspace/backup.env && chmod 600 /etc/my-workspace/backup.env
```

**9.2 先手动备份一次**（第一次会自动初始化仓库）：

```bash
set -a && . /etc/my-workspace/backup.env && set +a && make backup
```

输出 `备份完成：<快照 ID> <时间>` 即成功。

**9.3 每天自动备份**：`crontab -e` 加一行（每天 3:17）：

```cron
17 3 * * * cd /srv/my-workspace && set -a && . /etc/my-workspace/backup.env && set +a && make backup >> backups/backup.log 2>&1
```

**9.4 每月做一次恢复演练**：

```bash
set -a && . /etc/my-workspace/backup.env && set +a && make restore-verify
```

它在另一个独立的 Compose 项目里恢复最新快照、迁移、冒烟检查后删除，正在运行的服务不受影响。

n8n、AFFiNE 是独立部署的，各自备份（手册第 14.1 节）。服务器损坏后的真实恢复步骤见手册第 14.5 节——
那时需要第 4 步里另外保存的 `.env`。

---

## 10. 升级与回滚

升级前先看手册第 5 节和 [`release-checklist.md`](release-checklist.md)。常规流程：

```bash
git rev-parse HEAD && docker compose exec -T api alembic current
```

记下当前的提交号与迁移版本（回滚时要用），然后备份，并把 `.env` 另存一份：

```bash
set -a && . /etc/my-workspace/backup.env && set +a && make backup
```

```bash
git fetch && git checkout <新版本的提交或 tag>
```

```bash
make env
```

```bash
docker compose build && docker compose run --rm --no-deps -T api alembic upgrade head
```

```bash
make up && make smoke
```

- `make env` 只补上新增的变量，不改已有的值；
- 先构建、再迁移：迁移通常只新增对象，旧版本在迁移期间照常服务（`0020` 例外：它删除 Vikunja 的数据，迁移期间旧版本的任务页可能出错，`make up` 换上新版本后恢复）；
- 出问题时按手册第 5.3 节回滚：没有新迁移就 `git checkout <旧提交>` 再 `make up`；有新迁移先用新代码 `alembic downgrade` 到升级前的版本；
  更严重时从升级前的快照恢复。

**从带 Vikunja 的旧版本升级**（T-0082）：迁移 `0020` 把已经同步到工作台的 Vikunja 项目与任务转成工作台自己的数据
（ID 不变，收藏关联与搜索照常），然后删除 Vikunja 集成与 Webhook 收件箱。只转换工作台里已有的同步结果，
升级前可以先在旧版本的“集成设置”里对 Vikunja 点一次同步。

- 一键脚本升级（`sudo bash scripts/deploy.sh upgrade`）在迁移之后自动：停止并删除以前的 Vikunja 容器与网络
  （Compose 项目 `my-workspace-vikunja`），删除 `infra/caddy/apps-enabled/vikunja.caddy` 并重启 Caddy，清空 `.env` 的 `VIKUNJA_DOMAIN`。
  **数据卷保留**：最后打印 `docker volume rm …`，确认不再需要以前的数据后再手动执行。
- 手动升级时，迁移之后自己做：`docker ps -aq --filter label=com.docker.compose.project=my-workspace-vikunja | xargs -r docker rm -f`；
  有 `infra/caddy/apps-enabled/vikunja.caddy` 就删掉，再 `docker compose restart caddy`。
- `.env` 里旧的 `VIKUNJA_*`、`WEBHOOK_BASE_URL` 不再读取，可以留着。降级到 `0020` 之前只恢复表结构，工作台的项目与任务会被删除。

---

## 11. 日常运维速查

| 要做的事 | 命令 |
|---|---|
| 查看服务状态 / 日志 | `make ps` / `make logs` |
| 重启并应用 `.env` 的修改 | `make up` |
| 停止（保留数据） | `make down` ——**不要**用 `docker compose down -v`，`-v` 会删除数据卷 |
| 冒烟检查 / 完整端到端冒烟 | `make smoke` / `make smoke-full`（后者需要 `tests/e2e` 的依赖，手册第 15.2 节） |
| 备份 / 恢复演练 | `make backup` / `make restore-verify` |
| 安全扫描 / 性能基准 | `make scan` / `make perf`（手册第 15 节） |
| 轮换加密密钥 | 手册第 6.1 节 |
| 查看 n8n / OpenIM 日志 | `make n8n-logs` / `make openim-logs` |

“运维”页能看到各服务的状态；监控部署后还有请求量、延迟等预设指标。

---

## 12. 常见问题

- **登录或首次安装返回 `403 CSRF_FAILED`**：浏览器地址栏里的地址和 `PUBLIC_BASE_URL` 不一致（协议、域名、端口都要一致）。
  改 `.env` 后 `make up`。用多个地址访问时，其余的加进 `CORS_ALLOWED_ORIGINS`（逗号分隔）。
- **HTTPS 证书没签下来**（浏览器提示证书错误，或 `make up` 后 443 连不上）：
  - `getent hosts <域名>` 确认解析到了这台服务器；
  - 80 与 443 对公网开放（防火墙与云安全组都要放行）；
  - `docker compose logs caddy` 里看 Let's Encrypt 的报错。Let's Encrypt 对失败次数有限制，排查清楚再重试。
- **`make up` 时提示 `required variable SITE_DOMAIN is missing a value`**：启用了 `compose.https.yaml`，但 `SITE_DOMAIN` 或 `ACME_EMAIL` 没填。
- **`make up` 一直在等健康检查**：`make logs` 看是哪个服务起不来；`docker compose ps -a` 看退出码。
- **一键脚本装好 Python 3.12 后，`python3 --version` 还是 3.9 / 3.11**：正常。脚本不替换系统自带的 `python3`（apt 等系统工具依赖它），
  3.12 是另装的，命令是 `python3.12`（`/usr/local/bin/python3.12` → `/opt/my-workspace/python-3.12.16`），`make` 与备份脚本会优先用它。
  不要用 `update-alternatives` 或改链接的方式把 `python3` 指向 3.12。清空项目不会删除它；不需要时删除 `/usr/local/bin/python3.12` 与 `/opt/my-workspace/` 即可。
- **`make env` / `make up` 提示“`.env` 有问题……占位符”**，或者 api 起不来、`docker compose logs api` 里是
  `CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制`：`.env` 不是 `make env` 生成的（多半是从 `.env.example` 复制的），或者密钥被改坏了。
  - 全新安装、还没有任何数据：重新生成。已经 `make up` 过的，PostgreSQL 数据卷已经用占位符密码初始化：重新生成后按下面“改了 `.env` 里的数据库密码后连不上”同步密码；或者连同刚建的空数据卷一起删掉
    （`docker compose down -v` 会删除本项目的**全部**数据卷，只在确认没有数据时执行）：

    ```bash
    docker compose down -v
    ```

    ```bash
    mv .env .env.old && make env
    ```

    再把旧文件里改过的设置（`APP_ENV`、`PUBLIC_BASE_URL`、`COMPOSE_FILE`、`SITE_DOMAIN`、`ACME_EMAIL`、`HTTP_BIND` 等）抄进新的 `.env`，然后 `make up && make migrate`。
  - 只有加密密钥格式不对、api 从没启动成功过（还没有用它加密任何凭证）：直接换一个新密钥：

    ```bash
    sed -i "s/^CREDENTIAL_ENCRYPTION_KEY=.*/CREDENTIAL_ENCRYPTION_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')/" .env
    ```

  - 已经在使用的系统：不要直接换 `CREDENTIAL_ENCRYPTION_KEY`（已保存的凭证会解不开），按手册第 6.1 节轮换。
- **端口 8080 被占用**：改 `.env` 的 `HTTP_PORT`（用 IP 直接访问时 `PUBLIC_BASE_URL` 也一起改），再 `make up`。
- **登录返回 `429 RATE_LIMITED`**：15 分钟内同一账号失败 5 次或同一 IP 失败 10 次，等提示的秒数后自动解除；立即解除的方法见手册第 4 节。
- **改了 `.env` 里的数据库密码后连不上**（`make migrate` 或 api 日志里是 `password authentication failed for user "workspace"`）：
  PostgreSQL 只在第一次初始化数据卷时读取密码，之后改 `.env` 不会改数据库里的密码。
  例如先用复制来的 `.env`（密码 `CHANGE_ME`）`make up` 过，再重新 `make env` 生成了新密码。
  用下面的命令把数据库里的密码改成 `.env` 里的 `POSTGRES_PASSWORD`。数据不受影响；容器内经本地连接执行，不需要旧密码；
  密码经标准输入传给 `psql`，不会出现在命令行参数里：

  ```bash
  printf "ALTER USER workspace WITH PASSWORD '%s';\n" "$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)" | docker compose exec -T postgres psql -U workspace -d workspace
  ```

  看到 `ALTER ROLE` 就成功了，再运行 `make migrate`。
  `.env` 里改过 `POSTGRES_USER` / `POSTGRES_DB` 的，把命令里的 `workspace` 换成对应的值。
- **集成显示“需要重新连接”**：第三方 Token 失效或被收回了权限，在对方系统里重新创建 Token，粘贴到集成设置并重新测试连接。

更多排错见手册第 4 节；安全相关的设计与已知风险见 [`threat-model.md`](threat-model.md)。
