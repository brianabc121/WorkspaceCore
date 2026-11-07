# OpenClaw 服务器检查清单（上线和每次改配置后）

依据：My Workspace `docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`（下称 OC-0 ADR）。

## 安装

- [ ] **每个实例一个目录**：`~/myworkspace-openclaw/<实例短号>/`（README.md 里写了这个实例的短号与 Compose 项目名）。
      一台服务器可以放多个实例：每个实例是一个 Compose 项目（`compose.yaml` 第一行的 `name`），有自己的网络，
      互相连不到对方的本机地址；共享的只有 Docker、镜像缓存、登录账号与 crontab。
- [ ] 工作台地址要从容器里访问得到：OpenClaw 不再用宿主网络，**不能是 `127.0.0.1` / `localhost`**，
      宿主机 `/etc/hosts` 里的名字在容器里也查不到（内网地址请用 IP 或内网 DNS 里的名字）。
- [ ] **这个目录只给运行 OpenClaw 的用户读写**：`chown -R 1000:1000 . && chmod 700 .`（`state/` 是 OpenClaw 的配置与状态）；`myworkspace.env` 与 `tls/client.key`（外网模式）权限 600。
      里面有实例凭证；泄露后到工作台的实例页吊销并重新生成接入包。
- [ ] `bin/` 下三个程序：**属主是运行 OpenClaw 的用户**（官方镜像里是 uid 1000）、权限 0755、不是符号链接（OC-0 ADR F8）：
      `chown -R 1000:1000 bin && chmod 0755 bin/*`
- [ ] 插件（**第一次启动之前**）：核对 `@openim/openclaw-channel@0.3.3` 的 tarball sha256 是
      `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`，然后用一份临时的空配置安装
      （`state/openclaw.json` 里已经写了这个插件提供的 `openim` 渠道，插件装好之前 OpenClaw 认为这份配置无效、拒绝安装）：
      `echo '{}' > state/plugin-install.json && chown 1000:1000 state/plugin-install.json`
      先起这个实例的网络（`net` 服务，安装插件要联网）：`docker compose up -d net`
      `docker compose run --rm --no-deps -e OPENCLAW_CONFIG_PATH=/home/node/.openclaw/plugin-install.json --entrypoint node openclaw openclaw.mjs plugins install @openim/openclaw-channel@0.3.3 --force --accept-capabilities`
      然后打上工作台的补丁（自动回复被拒收时把原因放回会话，智能体下一轮看得到；T-0120）：
      `docker compose run --rm --no-deps --entrypoint node openclaw /opt/myworkspace/bin/channel-patch.mjs`
      输出 `plugin_patch_applied`（或重复运行时的 `plugin_patch_already_applied`）才算成功；它核对原文件与结果的 sha256，
      不是 0.3.3 的原文件就不改。装好后再 `docker compose up -d`。升级插件前重新做 OC-0 ADR F9 的审查，并更新补丁。
- [ ] 已经部署过的实例（T-0120 之前）：换上新的接入包后同样运行上面的打补丁命令，再 `docker compose up -d`
      （新凭证会让容器重建）。实例页不再提示“旧接入包”、上报的插件版本是 `0.3.3+myworkspace.1` 就对了。
- [ ] `state/openclaw.json` 里的模型服务商换成实际使用的那一家；密钥 ID 与工作台密钥库里给这个实例的授权一致。
      `${VAR}` 由 OpenClaw 从进程环境（`myworkspace.env`）读取，不要把明文写进这个文件；也不要加 `_comment` 之类的说明键（OpenClaw 会判为无效配置）。
- [ ] 冷启动要 2 ~ 4 分钟：启动时 doctor 会联网检查插件更新，没有外网时要等超时（OC-0 ADR F5）。

- [ ] 网络：`net` 服务只运行 `sleep`，持有这个实例的网络命名空间；`openclaw`、`tunnel`、`image-proxy` 用
      `network_mode: "service:net"` 加入，共用 127.0.0.1。不要单独停掉 `net`（加入它的容器会断网，要连同它们一起重启）。
- [ ] 外网模式：`tunnel` 服务（本机隧道）在 127.0.0.1:18443 监听，用 `tls/` 里的客户端证书连工作台外网入口；
      工作台外网入口的域名在这个实例里解析到 127.0.0.1（`net` 服务的 `extra_hosts`），
      隧道自己挂 `tls/hosts` 当 `/etc/hosts`，连真实的入口地址。
      入口用私有 CA 签发的证书时，在 `tunnel` 的命令里加 `--cacert=<CA 证书>`；接入包里有 `tls/ca.pem`（入口证书由工作台内部 CA 签发）时已经配好，不用改。
      客户端证书 90 天到期，到期前重新生成接入包。
- [ ] 内网模式、且工作台也有外网入口时：`image-proxy` 服务（本机取图代理）在 127.0.0.1:18443 监听，
      不带客户端证书转发到工作台内网入口，只用于取工作台发来的图片；外网入口的域名在这个实例里解析到 127.0.0.1。
- [ ] 资源上限（可选）：在工作台的实例设置里填内存、CPU 上限，重新部署后 `openclaw` 服务带上 `mem_limit` / `cpus`；
      没填就不限制。
      内网入口用私有 CA 签发的证书时，在 `image-proxy` 的命令里加 `--cacert=<CA 证书>`。

## 加固

- [ ] Gateway 只监听本机（`bind: loopback`），Compose 不映射任何端口。
- [ ] `node openclaw.mjs security audit --deep`、`doctor`、`health` 通过。
- [ ] `tools.exec.security = "deny"`；没有挂载 Docker socket；没有启用沙箱。
- [ ] `plugins.allow` 只有 `openclaw-channel`；没有安装其他插件和技能。
- [ ] **不设置 `commands.ownerAllowFrom`**：任何 IM 账号都不能在聊天里管理 OpenClaw（OC-0 ADR F6）。
- [ ] `openim_send_image` / `openim_send_file` / `openim_send_video` 在 `tools.deny` 里（接入包默认已列入，不要删掉；OC-0 ADR F3）。
- [ ] `message`、`conversations_send`、`conversations_turn`、`conversations_list` 在 `tools.deny` 里（T-0120）：
      主动发消息只走工作台的 `im_send`（先用 `im_list_targets` 找目标），有发件记录、拒收原因、附件检查；自动回复不受影响。
- [ ] `requireMention: true`；`inboundWhitelist` 是接入包生成时的管理员与本工作空间的实例（工作台的管理员或实例变化后重新生成接入包）。
- [ ] `node openclaw.mjs secrets audit` 没有发现明文密钥。
- [ ] 日志级别和保留期已设置：OpenIM SDK 会把消息内容写进日志（OC-0 ADR F7）。

## 定时任务（宿主机 cron 或 systemd timer）

每一行末尾加上这个实例的标记 `# mw-openclaw:<实例短号>`；同一台服务器上的多个实例各有各的几行，
重新部署时只替换行尾正好是自己标记的那几行（SSH 自动部署就是这样做的）。

- [ ] 每 10 分钟取一次轮换后的模型密钥（OC-0 已核验，不用重启）：
      `*/10 * * * * cd <本目录> && docker compose exec -T openclaw node openclaw.mjs secrets reload --json`
- [ ] 每周重启一次，换上新的 IM 令牌（令牌只在启动时领取，OC-0 ADR F1）：
      `17 4 * * 1 cd <本目录> && docker compose restart openclaw`
- [ ] 每分钟上报 token 用量（任务时间轴，T-0104）：在容器里调网关的 `sessions.usage`（只读），把每个会话今天的累计 token 发给工作台；
      会话只报 key 的 SHA-256，不发消息内容。没配这一行时，工作台的时间轴显示“未上报”：
      `* * * * * cd <本目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/usage-report.mjs`
- [ ] 每分钟上报服务器状态（T-0113、T-0146）：在容器里读 `/proc` 与磁盘统计（整台服务器的 CPU 核心数与负载、内存、两个分区、开机时长），
      这个实例自己容器的 cgroup（CPU 用量、内存、进程数）、数据目录大小（每 10 分钟一次）、自己网卡的收发字节，
      以及服务器指纹（`boot_id` 的 HMAC，盐是 `MYWS_HOST_SALT`，不报原值；同一台服务器上的实例指纹相同）。
      只发数字，不发进程、文件名、IP。没配这一行时，实例页的“服务器状态”显示“未上报”：
      `* * * * * cd <本目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/host-report.mjs`
- [ ] 每分钟领取唤醒（T-0121）：工作台到点叫醒智能体跑一轮（它自己用 `schedule_wakeup` 安排的，或管理员在实例页建的）。
      脚本领取到期的唤醒，在容器里运行 `openclaw agent --session-key <会话> --message-file <消息>`，再把结果报回工作台；
      同一时间只跑一个，一轮最多 10 分钟。没配这一行时唤醒不会执行，实例页的唤醒一直显示“待执行”：
      `* * * * * cd <本目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/wakeup.mjs`
- [ ] 每分钟领取终止令（T-0126）：管理员（或有权限的智能体）叫停这个实例正在执行的任务时，脚本在容器里用网关的
      `sessions.list` / `sessions.abort` 中止正在运行的会话，再报回工作台。没有终止令时不调网关。
      没配这一行时只有“软停止”生效（叫停后 5 分钟内它调用工作台工具会被拒绝），终止令 10 分钟后记为过期：
      `* * * * * cd <本目录> && docker compose exec -T openclaw node /opt/myworkspace/bin/stop-runs.mjs`
- [ ] 每晚复盘（知识库，T-0100）：OpenClaw 启动后在本目录运行一次 `sh setup-reflection.sh`。
      它用 `openclaw automations add` 建 OpenClaw 自己的定时任务（默认每天 22:00，提示词在 `reflection-prompt.md`）；
      重复运行只更新同一个任务。SSH 自动部署会自动运行这一步。

## 备份与回滚

- [ ] `docker compose exec openclaw node openclaw.mjs backup create --verify` 能在本机跑通；回滚要连同备份的状态一起恢复。
