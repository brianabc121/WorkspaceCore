# 只读盘点命令

所有 SSH 均使用用户已确认的 ED25519 公钥，专用本地 known_hosts，参数：

```bash
ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/tmp/t0224-trusted-known-hosts -o ConnectTimeout=10 \
  -o "ProxyCommand=$SOCAT - PROXY:proxy:%h:%p,proxyport=8088,resolve=0" \
  root@TARGET 'bash -s'
```

`SOCAT` 来自平台技能的校验安装器；TARGET 分别为 A、B 的已授权 Tailscale IP。

远端执行以下只读命令（输出见对应 inventory 文件；不存在的命令/路径按条件跳过）：

```bash
date -u +%FT%TZ
hostname
id
sha256sum /etc/machine-id
cat /etc/os-release
uname -srmo
systemd-detect-virt
nproc
lscpu | sed -n '1,24p'
free -m
df -hT
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS
ip -br address
ip -4 route
ss -lntup
systemctl list-units --type=service --state=running --no-pager --no-legend
for binary in docker podman kubectl k3s containerd ctr runsc helm cilium python3; do command -v "$binary" || true; done
if command -v docker >/dev/null; then
  docker version --format 'Server={{.Server.Version}}'
  docker ps -a --format 'name={{.Names}} image={{.Image}} status={{.Status}} ports={{.Ports}}'
  docker stats --no-stream --format 'name={{.Name}} cpu={{.CPUPerc}} memory={{.MemUsage}}'
  docker network ls
  docker volume ls
fi
for path in /etc/rancher/k3s/k3s.yaml /etc/kubernetes/admin.conf /root/.kube/config /etc/containerd/config.toml /etc/workspace/runtime-v2 /var/lib/rancher/k3s /opt/workspace /opt/t0224 /opt/workspace-runtime-v2-test; do
  if test -e "$path"; then stat -c '%A %U:%G %n' "$path"; fi
done
ps -eo pid,comm,etime | grep -Ei 'k3s|cilium|runsc|kubectl|acceptance|pytest|workspace|runner|tailscale' || true
```

补充核对：

```bash
for path in /sys/class/dmi/id/product_uuid /proc/sys/kernel/random/boot_id; do sha256sum "$path"; done
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT
command -v systemd-nspawn || true
command -v unshare || true
command -v qemu-system-x86_64 || true
test -e /dev/kvm && ls -l /dev/kvm
stat -fc %T /sys/fs/cgroup
cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null
for path in /opt /srv /root; do
  find "$path" -mindepth 1 -maxdepth 2 -type d \( -iname '*workspace*' -o -iname '*0224*' -o -iname '*k3s*' -o -iname '*acceptance*' \) -print 2>/dev/null
done
```

对 A 已有 `/opt/my-workspace`，只读检查目录元数据、指定深度内 Compose/.env/验收文件名称及 Git 是否存在；用 Python 仅读取 `.env` 中 `RUNNER_NETWORK_VERIFIED`、`RUNTIME_V2_ENABLED` 两个键，未打印其他配置值。结果为 `.env: absent`，未发现 `.git`。

双向节点探测（peer 分别为另一台服务器）：

```python
import socket, sys, time
host = sys.argv[1]
start = time.monotonic()
try:
    with socket.create_connection((host, 22), 5) as s:
        s.settimeout(5)
        print('peer=' + host, 'tcp22=connected',
              'banner=' + s.recv(256).decode().strip(),
              'elapsed_ms=' + str(round((time.monotonic() - start) * 1000)))
except OSError as e:
    print('peer=' + host, 'tcp22=failed', 'error=' + type(e).__name__ + ':' + str(e))
    sys.exit(1)
```

Tailscale/防火墙只读核对：

```bash
# JSON 仅输出 BackendState、Self HostName/TailscaleIPs/Online 和匹配目标 IP 的 Peer 状态；不输出用户信息及其他节点。
tailscale status --json
timeout 12 tailscale ping --c=1 --timeout=5s --until-direct=false PEER
iptables -S INPUT
iptables -S OUTPUT
iptables -S FORWARD
iptables -S ts-input
iptables -S ts-forward
ls -l /etc/machine-id /var/lib/dbus/machine-id
```

未执行安装、重启、网络变更、磁盘挂载、容器/卷删除、远端数据库迁移或生产开关变更。
