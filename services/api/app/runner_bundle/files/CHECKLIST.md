# 运行服务器检查清单

工作台的“连接与工具 → 运行服务器 → 设置”可以通过 SSH 自动部署；手动部署时按下面做。

1. 一台专用的 Linux 服务器（Ubuntu 22.04 / 24.04 或 Debian 12，x86_64，内核 5.6 以上），不放其他业务数据，
   不和工作台、OpenClaw 共用。它要能访问工作台的接入入口（见 runner.env 里的 MYWS_RUNNER_URL）；
   外网入口没有配置域名时，compose.yaml 已把入口主机名指向工作台服务器的地址（extra_hosts）。
2. 把这个目录拷到服务器上（例如 /opt/myworkspace-runner），以 root 运行 `bash install.sh`：
   装锁定版本的 Docker 29.9.1、gVisor release-20261019.0（核对 sha512），然后启动 runner-agent。
   第一次装 gVisor 会重启一次 Docker；以后重新运行（升级接入包）不再重启，正在运行的环境不受影响。
3. 回到工作台的运行服务器页面，看到“在线”就好了。
4. runner.env 里有运行服务器凭证（只显示这一次）；重新下载接入包会换新凭证，旧的立即失效。

不需要开放任何入站端口。环境容器只能经 runner-agent 的出口代理访问公网；私有网段、本机、
云厂商元数据地址、工作台与 OpenClaw 服务器永远到不了。
