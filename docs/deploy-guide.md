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

脚本会把代码放到 `/srv/my-workspace`，然后显示菜单：

| 选项 | 做什么 |
|---|---|
| 1 部署 | 首次安装；已经装过时重新检查并修复（不会丢数据） |
| 2 升级 | 先加密备份数据库（`make backup`）与 `.env`，再拉取新代码、补 `.env` 新变量、重建、迁移、检查 |
| 3 清空 | 删除本项目全部容器、网络与数据卷（包括 Vikunja、n8n、OpenIM 的），可选先备份；要输入项目名确认 |
| 4 状态 | 服务状态、本机与 HTTPS 健康检查、是否已完成首次安装 |

部署时只问三个问题：

| 参数 | 例子 | 说明 |
|---|---|---|
| `SITE_DOMAIN` | `workspace.example.com` | 已解析到这台服务器的域名 |
| `ACME_EMAIL` | `you@example.com` | Let's Encrypt 证书联系邮箱 |
| `PUBLIC_BASE_URL` | `https://workspace.example.com` | 直接回车就是 `https://<域名>`；必须和浏览器地址栏完全一致 |

其余的都自动完成，并处理了部署时常见的坑：

- 缺 git / make / python3 / curl 时用 apt 安装；没有 Docker 时用官方脚本安装（都会先问）；Python 3.8 及以上即可（Debian 11 自带的 3.9 可以用，不需要为此升级系统）；
- `.env` 不存在就随机生成；是从 `.env.example` 复制来的（还有占位符）时，备份后重新生成；
- 写入 `APP_ENV=production`、`COMPOSE_FILE=compose.yaml:compose.https.yaml`、域名、邮箱与 `PUBLIC_BASE_URL`；
- 检查域名解析、80 / 443 是否被占用（例如 nginx）、ufw 是否放行；
- `make up` → `make migrate`（数据库密码和 `.env` 不一致时自动同步）→ `make smoke` → 用 GET 检查 `https://<域名>/healthz`；
- 还没创建管理员时显示安装凭据。

已经有代码时，在代码目录里直接运行，也可以直接指定动作：

```bash
sudo bash scripts/deploy.sh upgrade
```

无人值守（例如自动化）时用参数代替提问：

```bash
sudo bash scripts/deploy.sh install --domain workspace.example.com --email you@example.com --yes
```

清空在无人值守时还必须加 `--confirm my-workspace`。全部选项见 `sudo bash scripts/deploy.sh --help`。

脚本只部署工作台本身（下面的“最小”形态）。Vikunja、监控、n8n、OpenIM 等可选组件，以及异地备份，仍按第 8、9 步配置；
脚本跑完后，下面第 1 ~ 6 步都已完成，直接从第 7 步创建管理员开始。想了解每一步做了什么、或者手动部署，就按下面的步骤来。

---

## 0. 先选部署形态

| 形态 | 包含 | 适合 |
|---|---|---|
| **最小** | 工作台本身（Caddy、前端、API、Worker、PostgreSQL、Redis） | 先试用：首页、日历、通知、收藏、成员管理 |
| **常用** | 最小 + Vikunja（任务）+ GitHub（只读）+ 监控（Prometheus / Grafana） | 日常使用 |
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

需要：Docker（含 Compose v2 插件）、GNU Make、Python 3.8 及以上（只用来生成 `.env`；Debian 11 的 3.9、Ubuntu 20.04 的 3.8 都可以）、Git。

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
docker version && docker compose version && make --version | head -1 && python3 --version
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

- `CREDENTIAL_ENCRYPTION_KEY`：加密已保存的第三方凭证（Vikunja / GitHub Token 等），**丢了就解不开**；
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

云服务器还要在控制台的安全组里放行 80 和 443。**不要**对公网开放 8080、3000、3456、5678 这些端口。

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
- 容器之间的内部地址 `http://caddy:8080` 保持不变（Vikunja 的 Webhook 用它），宿主机上的 8080 仍只绑定本机。

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
> ssh -L 3456:127.0.0.1:3456 -L 3000:127.0.0.1:3000 -L 5678:127.0.0.1:5678 <用户>@<服务器>
> ```
> 然后在本机浏览器打开 `http://localhost:3456`（Vikunja）、`http://localhost:3000`（Grafana）、`http://localhost:5678`（n8n）。

### 8.1 Vikunja（任务）

```bash
make vikunja-up
```

```bash
make vikunja-user
```

1. `make vikunja-user` 按提示创建 Vikunja 用户（Vikunja 关闭了自助注册）；
2. 登录 Vikunja 原生界面，在设置里创建 API Token（最小权限见 `decisions/2026-10-30-vikunja-2.7.1-verification.md` 第 3 节；
   要实时同步，给上 `projects_webhooks` 权限）；
3. 工作台“集成设置”里新建 Vikunja 集成：服务地址填 `http://vikunja:3456`，浏览器地址填 `.env` 里的 `VIKUNJA_PUBLIC_URL`，粘贴 Token，
   保存后点“测试连接”。通过后会立即同步项目与任务。

细节（Webhook、对账、死信排查）见手册第 8 节。Vikunja 的数据要单独备份（`vikunja dump`）。

### 8.2 GitHub（只读）

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**：
   只选要看的仓库，Repository permissions 只给 **Read-only** 的 Metadata、Issues、Pull requests、Actions；
2. 工作台“集成设置”里新建 GitHub 集成，粘贴 Token，填仓库（`owner/repo`，最多 20 个），点“测试连接”。

不要用经典 Token（`ghp_` 开头）。服务器要能访问 `https://api.github.com`。细节见手册第 10 节。

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

```bash
make n8n-up
```

1. 确认 `.env` 的 `INTEGRATION_ALLOWED_HOSTS` 里有 `n8n`（新生成的 `.env` 默认是 `vikunja,n8n`），然后 `make up`；
2. 打开 n8n 编辑器，创建 owner 账号；创建只读的 API Key（`workflow:list`、`workflow:read`、`execution:list`、`execution:read`）
   和名为 `X-Workspace-Trigger` 的 Header Auth 凭据；工作流用 Webhook 节点开头；
3. 工作台“自动化”页：填连接（`base_url` 为 `http://n8n:5678`），测试连接，登记白名单工作流。

`N8N_ENCRYPTION_KEY` 要和备份分开保存。完整步骤见手册第 12.4 节。

### 8.6 OpenClaw 与 IM（V2，可选）

让部署在其他服务器上的 OpenClaw 实例接入工作台：管理员在工作台里和实例单聊、群聊；实例的写操作都要在工作台审批。
这一部分组件较多，按手册第 18.4 节的顺序做，要点：

1. 准备接入入口的证书，在 `.env` 里填 `AGENT_*`（手册第 16.1 节）；
2. `make openim-build`（约 8 GB 临时磁盘，构建打过补丁的 OpenIM）；
3. `make agents-up`（导出内部 CA、启动接入入口 agent-gateway）；
4. `make openim-up`，在 `.env` 里设置 `OPENIM_API_URL=http://openim-server:10002`、`OPENIM_OBJECT_URL=http://garage:3900`，再 `make up`；
5. “运维”页里 OpenIM 显示“正常”，打开“消息”页，管理员的 IM 账号会自动注册；
6. “OpenClaw 实例”页登记实例、授权密钥、下载接入包，到 OpenClaw 服务器上按包里的 `CHECKLIST.md` 部署（手册第 16.2、18.3 节）。

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

Vikunja、n8n、AFFiNE 是独立部署的，各自备份（手册第 14.1 节）。服务器损坏后的真实恢复步骤见手册第 14.5 节——
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
- 先构建、再迁移：迁移只新增对象，旧版本在迁移期间照常服务；
- 出问题时按手册第 5.3 节回滚：没有新迁移就 `git checkout <旧提交>` 再 `make up`；有新迁移先用新代码 `alembic downgrade` 到升级前的版本；
  更严重时从升级前的快照恢复。

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
| 查看 Vikunja / n8n / OpenIM 日志 | `make vikunja-logs` / `make n8n-logs` / `make openim-logs` |

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
