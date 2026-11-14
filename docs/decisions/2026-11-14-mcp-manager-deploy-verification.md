# 一键部署 MCP 管理器的核验记录（T-0233，docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md）

> 规则（`docs/AGENTS.md` 第 8 节）：第三方的接口与行为必须在锁定版本上核验，不按名称猜。
> 本记录是方案里依赖的三件第三方行为的实测结果，都在本会话自己的环境里做（2026-11-14）。

## 1. 版本

| 组件 | 版本 | 说明 |
|---|---|---|
| OpenSSH（sshd） | 9.6p1（Ubuntu 24.04 的 `openssh-server 1:9.6p1-3ubuntu13.20`） | 本机起的 sshd，只监听回环地址，专用的测试账号 |
| asyncssh（工作台的 SSH 客户端） | 2.24.1 | `services/api/uv.lock` 锁定的版本 |
| Docker Engine | 29.9.2 | 本会话自己的 `dockerd` |

## 2. 只能转发一个端口的密钥（方案第 4 节）

装进 `authorized_keys` 的那一行：

```text
restrict,port-forwarding,permitopen="127.0.0.1:18787",command="/bin/false" ssh-ed25519 AAAA… myworkspace-mcp-<编号>
```

用 asyncssh 2.24.1 以这把密钥登录真实的 sshd，逐项结果：

| 做什么 | 结果 |
|---|---|
| `forward_local_port` 到 `127.0.0.1:18787`，经它发 HTTP 请求 | 200，拿到那个端口上的服务的回答 |
| 执行命令（`touch <标记文件>; echo ran`） | 退出码 1，没有输出，标记文件没有出现——跑的是固定命令 `/bin/false` |
| 转发到别的端口（`127.0.0.1:18788`，上面有服务在听） | 被拒绝：`ChannelOpenError` code 1（administratively prohibited） |
| 目的端口上没有东西在听 | `ChannelOpenError` code 2（connect failed，Connection refused） |
| 申请终端（PTY） | 被拒绝：`PTY request failed` |
| sshd 配了 `AllowTcpForwarding no` | 本机的转发端口能开，但开通道时被拒绝：code 1（administratively prohibited） |

结论与用法：

- `restrict` 关掉终端、代理转发、X11 等，但**不禁止执行命令**（OpenSSH `sshd(8)` 的 AUTHORIZED_KEYS FILE FORMAT 一节）；
  所以必须再加固定命令。加了 `command="/bin/false"` 后客户端要执行什么都只会跑它。
- 工作台用 code 1 判断“这台服务器不允许端口转发”（部署任务马上失败并说明原因），用 code 2 判断“管理器还没起来”（继续等）。
- 测试里的假 SSH 服务（`services/api/tests/test_module_llm_bridge_deploy.py` 的 `_McpServer`）按上表的行为实现。

来源：<https://man.openbsd.org/sshd#AUTHORIZED_KEYS_FILE_FORMAT>。

## 3. 不重启 Docker 就登记新的运行时（方案第 8 节，gVisor 安装）

步骤：先起一个容器并记下它的 `StartedAt`；往 `/etc/docker/daemon.json` 写入一个新的运行时
（`{"runtimes":{"mwtest":{"path":"/usr/bin/runc"}}}`）；给 `dockerd` 发 `SIGHUP`；看 `docker info`。

| 检查 | 结果 |
|---|---|
| 发信号前 `docker info` 的 Runtimes | `io.containerd.runc.v2 runc` |
| 发信号后 | `io.containerd.runc.v2 mwtest runc`——新运行时登记上了 |
| 那个容器 | 仍在运行，`StartedAt` 没变 |

结论：`runtimes` 是 dockerd 可以重新加载的配置项，`systemctl reload docker`（发 SIGHUP）即可，不需要重启。
`install-gvisor.sh` 改成先重新加载；重新加载后仍没有登记时才考虑重启，并且服务器上有运行中的 MCP 管理器时
要管理员在页面上确认。

来源：<https://docs.docker.com/reference/cli/dockerd/#configuration-reload-behavior>。

## 4. 经 sudo 用 Docker（方案第 2 节）

容器服务器的登录账号按设计不在 docker 组（`docs/decisions/2026-11-06-runner-verification.md`）。
部署 MCP 管理器时沿用那里已经实测过的写法：`sudo -S -p '' -k <命令>`，密码从标准输入给、不出现在命令行里，
`-k` 不用上一次缓存的授权。免密 sudo 用 `sudo -n` 探测。这条路在后端测试里用桩程序覆盖
（`test_deploy_to_a_runner_host_uses_sudo`）；真实的 `sudo -S` 由容器服务器的 SSH 部署验收覆盖（同一种写法）。

## 5. 持续验收

CI 的 `agents` job（`.github/workflows/ci-infra.yml`）在真实的 sshd 和真实的 Docker 上跑
`tests/e2e/mcp_manager_deploy_acceptance.py`：工作台容器用第 2 节那种密钥经 sshd 的端口转发访问
只监听本机地址的管理器容器，并检查保护标签、清理不影响、重启、重新部署、卸载、移除服务器后照常能用。
