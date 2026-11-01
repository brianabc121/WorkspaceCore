# OpenClaw 服务器检查清单（上线和每次改配置后）

依据：My Workspace `docs/decisions/2026-10-31-v2-oc0-openclaw-openim-verification.md`（下称 OC-0 ADR）。

## 安装

- [ ] **这个目录只给运行 OpenClaw 的用户读写**：`chown -R 1000:1000 . && chmod 700 .`（`state/` 是 OpenClaw 的配置与状态）；`myworkspace.env` 与 `tls/client.key`（外网模式）权限 600。
      里面有实例凭证；泄露后到工作台的实例页吊销并重新生成接入包。
- [ ] `bin/` 下两个程序：**属主是运行 OpenClaw 的用户**（官方镜像里是 uid 1000）、权限 0755、不是符号链接（OC-0 ADR F8）：
      `chown -R 1000:1000 bin && chmod 0755 bin/*`
- [ ] 插件（**第一次启动之前**）：核对 `@openim/openclaw-channel@0.3.3` 的 tarball sha256 是
      `3d10dca0edcba0ecdb0dc915e7cf1ddf5a8453165de13dec76bbe5ef85607643`，然后用一份临时的空配置安装
      （`state/openclaw.json` 里已经写了这个插件提供的 `openim` 渠道，插件装好之前 OpenClaw 认为这份配置无效、拒绝安装）：
      `echo '{}' > state/plugin-install.json && chown 1000:1000 state/plugin-install.json`
      `docker compose run --rm --no-deps -e OPENCLAW_CONFIG_PATH=/home/node/.openclaw/plugin-install.json --entrypoint node openclaw openclaw.mjs plugins install @openim/openclaw-channel@0.3.3 --force --accept-capabilities`
      装好后再 `docker compose up -d`。升级插件前重新做 OC-0 ADR F9 的审查。
- [ ] `state/openclaw.json` 里的模型服务商换成实际使用的那一家；密钥 ID 与工作台密钥库里给这个实例的授权一致。
      `${VAR}` 由 OpenClaw 从进程环境（`myworkspace.env`）读取，不要把明文写进这个文件；也不要加 `_comment` 之类的说明键（OpenClaw 会判为无效配置）。
- [ ] 冷启动要 2 ~ 4 分钟：启动时 doctor 会联网检查插件更新，没有外网时要等超时（OC-0 ADR F5）。

- [ ] 外网模式：`tunnel` 服务（本机隧道）在 127.0.0.1:18443 监听，用 `tls/` 里的客户端证书连工作台外网入口；
      工作台外网入口的域名在 OpenClaw 容器里解析到 127.0.0.1（`compose.yaml` 的 `extra_hosts`）。
      入口用私有 CA 签发的证书时，在 `tunnel` 的命令里加 `--cacert=<CA 证书>`。客户端证书 90 天到期，到期前重新生成接入包。
- [ ] 内网模式、且工作台也有外网入口时：`image-proxy` 服务（本机取图代理）在 127.0.0.1:18443 监听，
      不带客户端证书转发到工作台内网入口，只用于取工作台发来的图片；外网入口的域名在 OpenClaw 容器里解析到 127.0.0.1。
      内网入口用私有 CA 签发的证书时，在 `image-proxy` 的命令里加 `--cacert=<CA 证书>`。

## 加固

- [ ] Gateway 只监听本机（`bind: loopback`），Compose 不映射任何端口。
- [ ] `node openclaw.mjs security audit --deep`、`doctor`、`health` 通过。
- [ ] `tools.exec.security = "deny"`；没有挂载 Docker socket；没有启用沙箱。
- [ ] `plugins.allow` 只有 `openclaw-channel`；没有安装其他插件和技能。
- [ ] **不设置 `commands.ownerAllowFrom`**：任何 IM 账号都不能在聊天里管理 OpenClaw（OC-0 ADR F6）。
- [ ] `openim_send_image` / `openim_send_file` / `openim_send_video` 在 `tools.deny` 里（接入包默认已列入，不要删掉；OC-0 ADR F3）。
- [ ] `requireMention: true`；`inboundWhitelist` 是接入包生成时的管理员与本工作空间的实例（工作台的管理员或实例变化后重新生成接入包）。
- [ ] `node openclaw.mjs secrets audit` 没有发现明文密钥。
- [ ] 日志级别和保留期已设置：OpenIM SDK 会把消息内容写进日志（OC-0 ADR F7）。

## 定时任务（宿主机 cron 或 systemd timer）

- [ ] 每 10 分钟取一次轮换后的模型密钥（OC-0 已核验，不用重启）：
      `*/10 * * * * cd <本目录> && docker compose exec -T openclaw node openclaw.mjs secrets reload --json`
- [ ] 每周重启一次，换上新的 IM 令牌（令牌只在启动时领取，OC-0 ADR F1）：
      `17 4 * * 1 cd <本目录> && docker compose restart openclaw`

## 备份与回滚

- [ ] `docker compose exec openclaw node openclaw.mjs backup create --verify` 能在本机跑通；回滚要连同备份的状态一起恢复。
