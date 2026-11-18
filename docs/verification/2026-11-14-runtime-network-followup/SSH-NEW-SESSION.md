# 密钥更新后新开 Cloud 会话：SSH 只读复测

记录日期：2026-11-14。用户侧时间为 UTC+8，原始代理响应保留 GMT。本文摘录本新会话的实际工具结果；没有重新执行旧会话测试，也不覆盖旧失败证据。

## 新旧环境对照

|字段|旧“核验 Tailscale 主机身份”会话|本“重新连接并核验 Tailscale 身份”会话|
|---|---|---|
|environment_id|`ccarenv_b64_Y2NhcmVudl9iZGRiYTY5OWVkMDg4MTkxYWYyMjI3NWVkZjhmYjQ1OQ`|`ccarenv_b64_Y2NhcmVudl83OTRhMjQxZjkzNjg4MTkxOTAyMDRlOTJmZWI2ODIxOA`|
|source_config_id|`0ae4f95f-4796-4e1d-916e-cb8ac9fc96bd~cecfg_6ae3b00c2934819b9bb8826788a25cae`|相同|
|source_config_version_id|`0ae4f95f-4796-4e1d-916e-cb8ac9fc96bd~cecfgver_6af72486b7a0819b844f25d63c73155f`|`0ae4f95f-4796-4e1d-916e-cb8ac9fc96bd~cecfgver_6af7dbc68128819b8c94f2509863de6a`|
|spec_revision / observed_spec_revision|1 / 1|1 / 1|
|实际 TCP22 结果|A/B 均约5秒后503 connection timeout|A/B 均200并收到SSH banner|

旧环境字段及07:14 UTC复测来自旧会话工具输出；更早失败见[历史诊断](README.md)。新环境由本会话 `cloud_environment.environment_status({})` 读取：`observations_current=true`、`connectivity=connected`、`network_policy.state=enforced`。`failure=null` 本身不作为 VPN 可达性证据。

## 1. 平台状态与实际 TCP

通过批准的外层执行器，以 Python 标准库读取 `http://proxy:8099/status`，仅输出所需 VPN 字段；随后并行对 `proxy:8088` 发出 `CONNECT 目标IP:22`，读取响应头与 SSH banner。命令退出码0。

实际输出摘录：

```jsonl
{"tailscale": {"configured": true, "state": "Running", "routing_ready": true, "observed_at": "2026-11-14T07:33:34.117418+00:00"}}
{"host": "100.72.60.100", "headers": "HTTP/1.1 200 OK\r\ndate: Sat, 14 Nov 2026 07:33:34 GMT\r\nserver: envoy", "body": "SSH-2.0-Tailscale\r\n"}
{"host": "100.121.210.36", "headers": "HTTP/1.1 200 OK\r\ndate: Sat, 14 Nov 2026 07:33:34 GMT\r\nserver: envoy", "body": "SSH-2.0-Tailscale\r\n"}
```

上面代码块为逐行 JSON 输出（JSON Lines），不是一个 JSON 对象。时间对应 UTC+8 的15:33:34。

## 2. 公钥比对与 SSH 命令

本机没有 Paramiko，使用已有 `/usr/bin/ssh`、`ssh-keyscan`，没有安装依赖。Python 临时 loopback relay 将本机连接转发到同一 `proxy:8088` CONNECT 路径，使用有限连接/读取/命令超时。

每台主机先用 `ssh-keyscan -T 10 -p 本地端口 -t ed25519 127.0.0.1` 采集候选，按公钥 blob 的 SHA256 与用户此前可信确认的指纹精确比较。匹配后才写临时专用 known_hosts，使用真实目标 IP 作主机身份。关键客户端参数与命令如下（端口和文件名由本次临时目录分配）：

```text
ssh -F /dev/null -p 本地端口
  -o HostKeyAlias=真实目标IP
  -o UserKnownHostsFile=本次专用known_hosts
  -o GlobalKnownHostsFile=/dev/null
  -o StrictHostKeyChecking=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
  root@127.0.0.1 'hostname; id'
```

实际结果：

```text
TARGET 100.72.60.100
Fingerprint SHA256:vbzwuvOu30pbIyANTCFBC/b4/At+uvpTB8kvl1QpKJE MATCH
exit 0 stdout: s1348279
uid=0(root) gid=0(root) groups=0(root)
 stderr:

TARGET 100.121.210.36
Fingerprint SHA256:vbzwuvOu30pbIyANTCFBC/b4/At+uvpTB8kvl1QpKJE MATCH
exit 0 stdout: s8006604
uid=0(root) gid=0(root) groups=0(root)
 stderr:
```

这是本次实际调用的摘录，排版合并了执行与轮询两段输出。外层验证进程最终退出0；两台 SSH 子进程分别退出0、标准错误均为空。进程结束后临时 relay 与专用 known_hosts 一并结束/清理，未来会话仍需恢复可信记录或重新比对。

## 已确认与未确认

- 已确认：新实例与新源配置版本下，两台目标均能通过平台 TCP 代理建立 SSH；主机公钥匹配，只读 `hostname/id` 成功。
- 固定运维流程：每次更新 Cloud 环境的 Tailscale 密钥后，Save → Publish/Republish → 从该环境新开 Cloud 会话 → 实测。依据[官方 VPN 更新流程](https://developers.openai.com/codex/environments/cloud-environments#private-networking-vpn)，已有运行实例需替换才能采用更新配置。
- 未确认：平台代理当前设备 IP/设备 ID、实际所用密钥的内容或所有属性、旧实例内部超时的具体根因。不能把历史 Codex IP 指认为本会话来源，也不能只凭本次恢复认定某个组件有确定缺陷。
- 本次没有修改服务器、ACL、标签、注册密钥或生产开关。没有执行部署、集群、公网或容量验收。
- 手册将 `src/dst` 示例统一改为角色标签是文档修订，不表示已把示例应用到真实管理端；按[长期手册](../../tailscale-development-guide.md#policy)核对两端、两层及设备实际标签。
