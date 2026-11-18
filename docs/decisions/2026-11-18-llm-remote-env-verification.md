# 核验记录：开发环境的“云端远程容器”（2026-11-18）

对应方案：`docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md` 第 16 节。核验人：claude（会话 `session_0171QGWM1UyBZv1vXTduEVXi`）。

## 环境

- Docker Engine 29.9.2（本会话自己的 `dockerd`，cgroup v1，overlayfs）；gVisor `runsc release-20261019.0`（官方 tarball，sha512 与 `services/api/app/runner_bundle/files/install-gvisor.sh` 里的值一致），以 `--add-runtime runsc=…` 接入。
- 镜像：第一阶段 `node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee`，
  把 `node` 拷到 `/opt/mw/cli/bin/node`，再 `npm install -g --prefix /opt/mw/cli @openai/codex@0.169.0 @anthropic-ai/claude-code@2.1.323 @tencent-ai/codebuddy-code@2.184.0`；
  第二阶段 `FROM debian:bookworm-slim`，`COPY --from=cli /opt/mw/cli /opt/mw/cli`，`PATH` 前面加 `/opt/mw/cli/bin`，建 uid 1000 的用户。
- 容器参数照容器服务器的默认值：`--user 1000:1000 --cap-drop ALL --security-opt no-new-privileges`，需要时 `--init`。
- 没有用任何真实账号，也没有发出真实的模型请求（下面 V3 / V6 用的是一个只记录、全部拒绝的本地代理）。

## 结果

| # | 事项 | 结果 |
|---|---|---|
| V8 | 命令行目录拷进不带 Node 的镜像能不能直接跑 | **通过**。`debian:bookworm-slim` 里 `node v22.23.5`、`codex-cli 0.169.0`、`2.1.323 (Claude Code)`、`2.184.0` 都正常输出版本；`setsid` 在 `/usr/bin`。整个 `/opt/mw/cli` 约 **1.2 GB**（`@openai` 404 MB、`@anthropic-ai` 346 MB、`@tencent-ai` 335 MB），镜像 1.85 GB |
| V2 | 三个命令行在 gVisor 里能不能跑 | **通过**（版本命令、启动、经代理出网；内核报 `4.19.0-gvisor`）。真实回合要用账号再跑一次 |
| V2 | Codex 自己的沙箱在容器里能不能用 | **不能**。0.169.0 的沙箱用 bubblewrap：runc（Docker 默认 seccomp、去掉全部 capability）里报 “No permissions to create a new namespace”；gVisor 里报 “bwrap: loopback: Failed RTM_NEWADDR: No child process”，打开网络（`sandbox_workspace_write.network_access=true`）也一样。`codex sandbox -P :workspace / :read-only` 实测 |
| V6 | CodeBuddy 走不走 `HTTPS_PROXY` | **走**。三个命令行都只经代理发 `CONNECT` |
| V3 | 必需的域名（部分） | 不登录、只用假 API 密钥时启动到第一次请求：Claude → `api.anthropic.com`；Codex → `api.openai.com`、`chatgpt.com`、`api.github.com`；CodeBuddy → `www.codebuddy.ai`、`download.codebuddy.cn`。**登录、刷新令牌、订阅账号下的域名还没测**，要用真实账号补 |
| V9 | `docker exec -d` + `setsid` 托管进程（gVisor） | **通过**。`docker exec -d` 0.04 秒返回，进程在后台继续；标准输入从文件读到；输出写文件，按字节偏移 `tail -c +N` 续读正常；`kill -TERM "-<进程组号>"` 停掉整组（包括后台的两个子进程），包装脚本写下退出码 143；`--init`（docker-init 为 1 号进程）回收孤儿。注意：Debian 的 `sh`（dash）里 `kill -TERM -- -N`、`kill -s TERM -N` 都报错，要写 `kill -TERM "-N"`；镜像里没有独立的 `/bin/kill` |
| V7 | `CODEBUDDY_IS_SANDBOX=1` 在 `-p` 模式下的效果 | **未测**：要有模型回合才看得出来 |
| V1 | Codex `exec --json` 的完整事件 | **未测**：要真实账号。不登录时已看到 `thread.started`（带 `thread_id`）、`turn.started`、`error`、`item.completed`（`type: error`） |
| V4 | 刷新令牌会不会轮换 | **未测**：要真实账号 |
| V5 | 在容器里经出口代理完成登录 | **未测**：要真实账号 |

## 对方案的影响

1. **远程容器里的 Codex 只提供“可执行命令”档**（`--dangerously-bypass-approvals-and-sandbox`，容器本身是沙箱）；只读、可写工作区两档在远程容器里对 Codex 不可选，页面写明原因。Claude、CodeBuddy 的三档靠工具限制实现，不依赖操作系统沙箱，照常提供。
2. **内置镜像大**：命令行目录约 1.2 GB，第一次构建要下载约 1 GB；建议容器服务器上只构建一次、所有远程容器共用这一层（`COPY --from` 同一个 digest）。
3. **托管进程的停止命令**用 `kill -TERM "-<进程组号>"`，10 秒后同样写法发 `KILL`；容器开 `Init`（容器服务器已经是这样）。
4. V1、V4、V5、V7 和 V3 的登录部分要真实账号，放进人类验收；在那之前：会刷新的登录按 D1 在容器里单独登录（不依赖 V4 的结论），默认网络档的域名清单先按上面实测到的加上各平台登录所需（核验前用“全部外网”兜底，页面写明）。
