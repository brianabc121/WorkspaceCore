# 2026-11-02 一键部署后自动接入 Vikunja、GitHub、n8n（T-0076）

- 状态：accepted（claude，任务范围内的技术决策）
- 关联：`scripts/deploy.sh`（T-0072）、`services/api/app/commands/connect.py`、`services/api/app/integrations/provisioning.py`；
  核验依据：`2026-10-30-vikunja-2.7.1-verification.md`（第 3 节）、`2026-10-30-github-rest-and-affine-verification.md`（第 3、4、10 节）、
  `2026-10-31-prometheus-grafana-n8n-verification.md`（第 5 节）

## 1. 背景

人类要求：首次安装后由程序自动配置 Vikunja，自动完成 GitHub 的认证、权限检查与同步，n8n 也要自动配置。
之前这三项都要管理员照着教程第 8 节手动操作：建账号、建 Token、复制粘贴到工作台“集成设置”和“自动化”页。

## 2. 决定

1. **管理员仍在浏览器创建**（人类选择）：首次安装页与安装凭据的流程不变。部署脚本在安装结束时提示去浏览器创建管理员，
   回到终端按回车后接着接入；可以跳过，以后运行 `deploy.sh connect`（菜单第 5 项）。
2. **接入在 api 容器里完成**（`python -m app.commands.connect`），调用与网页相同的 `IntegrationService` / `AutomationService`：
   - 以首次安装创建的管理员本人的身份（默认最早创建的用户与他唯一管理的工作空间；不唯一时用 `--email` / `--workspace`），
     先按网页同样的规则要求 admin 以上角色（`require_role`）；
   - 凭证照常加密保存、写审计（操作者是该管理员，没有会话与客户端 IP）；为此服务层的参数从 `AuthContext`
     放宽为只含 `user`、`client_ip` 的 `Actor` 协议，网页的请求不受影响；
   - 已经接入的组件只报告状态，不重复创建；更换凭证仍在网页上做。例外是 GitHub：仓库级的权限缺失不改变集成状态，
     所以脚本在 GitHub 已接入时询问要不要换 Token 或仓库，确认后用 `github --replace` 更新并重新检查。
3. **密码与 Token 只经标准输入传给容器**，不出现在命令行参数、进程列表、日志与命令输出里。例外：Vikunja 的命令行
   `vikunja user create` 只能用 `-p` 给密码（没有终端时读不了输入，实测报 `inappropriate ioctl for device`），
   密码在 Vikunja 容器内的这一个进程参数里短暂出现（不到 1 秒）。服务器只有管理员能登录，接受这个风险。
4. **脚本生成的账号密码只显示一次**（Vikunja 用户、n8n owner），提示保存到密码管理器；工作台不保存这两个密码。
   随机生成 23 位，满足 n8n 的规则（8 ~ 64 位、含数字与大写字母）。

## 3. 各组件做了什么（接口均在锁定版本上实测，2026-11-02）

### 3.1 Vikunja 2.7.1

| 步骤 | 接口 | 实测结果 |
|---|---|---|
| 建用户 | `vikunja user create -u -e -p` | 成功退出码 0；用户名已存在时退出码 1（`User with that username already exists`），脚本改为询问已有密码 |
| 登录 | `POST /api/v2/login {username, password}` | 返回 `{token}`（短期 JWT）；密码不对返回 **403** |
| 建 Token | `POST /api/v2/tokens`（Bearer JWT）`{title, permissions, expires_at}` | 返回 `{id, token: tk_…}`；服务端记录的权限与请求完全一致 |
| 吊销 | `DELETE /api/v2/tokens/{id}` | 200；工作台保存失败时用它吊销刚建的 Token |

- 权限：核验 ADR 第 3 节的最小权限 `projects: read_all, read_one`、`tasks: create, read_all, read_one, update, delete`，
  加 `projects_webhooks: create, read_all, update, delete`（自动注册 Webhook，实时同步）。不给标签权限。
- 有效期 365 天，写入集成的 `token_expires_at`（到期提醒沿用现有逻辑）。10 年的有效期 Vikunja 也接受，但选一年以便定期更换。
- 之后照常“测试连接”：通过后立即全量同步，并投递 Webhook 注册作业。

### 3.2 GitHub（只能半自动）

- GitHub 没有用 API 创建 Personal Access Token 的接口，Token 必须由用户在网页上生成。官方文档支持**预填链接**
  （[Pre-filling fine-grained personal access token details using URL parameters](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#pre-filling-fine-grained-personal-access-token-details-using-url-parameters)）：
  `https://github.com/settings/personal-access-tokens/new?name=My+Workspace&description=…&expires_in=90&issues=read&pull_requests=read&actions=read`
  （Metadata 只读自动带上）。仓库范围不能预填，由用户在页面上勾选。
- 脚本隐藏输入接收 Token，用 `GET /user/repos`（只需 Metadata 只读）列出 Token 能访问的仓库让用户挑选（最多 20 个）；
  然后建集成并“测试连接”：逐个仓库检查 Metadata、Issues、Pull requests、Actions 的读权限，缺哪个就报哪个，通过后开始同步。
- 只接受细粒度 Token（`github_pat_`）。`/user/repos` 按文档实现，**没有用真实 Token 实测**（本环境没有 GitHub Token；
  与核验 ADR 第 10 节一致）；列不出仓库时脚本改为手动输入 `owner/repo`。
- Token 的过期时间不自动填写（没有找到可靠的读取方式），与网页上一样是选填项。

### 3.3 n8n 2.45.7

建 owner、登录、建 API Key 与凭据只有**内部 REST**（`/rest/...`，不在 Public API 的 OpenAPI 里），在锁定的镜像
（`n8nio/n8n:2.45.7@sha256:9a8d8dce…`）上逐个实测：

| 步骤 | 接口 | 实测结果 |
|---|---|---|
| 是否已有 owner | `GET /rest/settings` → `data.userManagement.showSetupOnFirstLoad` | 新实例 `true`，建好 owner 后 `false` |
| 建 owner | `POST /rest/owner/setup {email, firstName, lastName, password}` | 只能成功一次，第二次返回 400；弱密码返回 400 |
| 登录 | `POST /rest/login {emailOrLdapLoginId, password}` | 设置 Cookie `n8n-auth`；密码不对返回 401 |
| 建 API Key | `POST /rest/api-keys {label, scopes, expiresAt: null}` | 返回 `data.rawApiKey`（只返回一次）与 `data.scopes` |
| 建凭据 | `POST /rest/credentials {name, type: "httpHeaderAuth", data: {name, value}}` | 返回 `data.id` |

- API Key 只要工作台需要的四个只读 scope（`workflow:list/read`、`execution:list/read`）；返回的 scope 与请求的不一致时不保存。
  实测这个 Key 能被 `N8nClient.check_token` 通过，调用 `POST /api/v1/credentials` 返回 403。
- 触发用的共享密钥随机生成（`secrets.token_urlsafe(32)`），在 n8n 里建成名为 `X-Workspace-Trigger` 的 Header Auth 凭据
  （与部署手册第 12.4 节的手动步骤同名），同时加密保存到工作台的 n8n 连接；工作流的 Webhook 节点直接选这个凭据。
- 工作流本身仍由用户在 n8n 里搭，搭好后到工作台“自动化”页登记白名单。
- 内部 REST 不是公开契约，升级 n8n 时要按第 5 节重新实测；不通时脚本报错并提示到“自动化”页手动配置，不影响已有连接。

## 4. 错误与幂等

- 外部 HTTP 走 `ConnectorHttp`：超时、错误归类、只重试读请求；写请求（建 Token、建 owner 等）不重试。
- 退出码：0 完成或已接入；1 接入失败（外部服务拒绝、测试连接没通过）；2 用法错误或前提不满足（还没有管理员、角色不够等）。
- 重复运行安全：已接入的跳过（GitHub 可选择替换）；Vikunja 用户已存在时询问密码；n8n 已有 owner 时询问其邮箱与密码。
- 不需要输入的容器调用都接 `/dev/null`：`docker compose exec -T` 会读走终端里提前敲下的回答（伪终端测试中发现）。
- Vikunja 的 Token 已建好但工作台保存失败时立即吊销；n8n 的 Key 或凭据建好后工作台保存失败，会在 n8n 里留下
  一个没人用的只读 Key / 凭据，提示里说明到 n8n 设置里删除即可（不影响安全：只读 Key、随机密钥）。

## 5. 升级时

- Vikunja、n8n 换版本时，先在新版本上重跑第 3 节的检查（`services/api/tests/test_connect_command.py` 覆盖了请求格式，
  真实行为要对新实例实测），再改 compose 里锁定的版本。
- GitHub 预填链接的参数名来自官方文档；文档变化时更新 `scripts/deploy.sh` 的 `GITHUB_TOKEN_URL`。
