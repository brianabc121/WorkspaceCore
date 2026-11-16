# 一键部署直连接入修复验证

## 指派与状态

人类要求完善一键部署、直接完成，并已卸载准备重新测试。实现基于 main `94e48cd`，位于 `codex/deploy-ingress-direct`。
已在 [集成队列 #235](https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6924742822) 提交摘要、owner codex 与 paths，等待登记集成者分配正式任务号及审查集成；不自行抢号或接管 main。
PR #239 的初始 head `ceaead9` 被 Claude 审查阻塞；本轮按 #50 C-9 修复，远端新候选的结果记录在 PR 和队列评论。

## 问题与结果

原脚本在域名模式清空 `AGENT_PUBLIC_ADDRESS`，网站走 Cloudflare 时客户端连接到不支持 9443 的代理。
新脚本区分证书域名和真实连接 IP，在前台收集并保存回答，后台按回答或既有配置执行；保留已保存地址，明确选择 `-` 才用 DNS。
域名模式不自动把私网网卡或代理 DNS 结果当作公网入口。无域名直接执行保留出站地址默认值，兼容 LAN / Tailscale；后台仍只使用已记录或已保存的地址。
旧内部 CA 域名入口在无人值守且没有地址回答时保持现状并记录待办，其他组件继续。IPv6 地址使用匹配的发布绑定与本机检查地址，入口只接受 IPv6。

证书、配置应用、入口启动及握手失败会记入部署待办。配置应用存在持久的 `ingress_pending` 标记，重跑会重新应用，直到本机 TLS 检查通过；成功后正常重跑不强制重建。
本机成功明确标为本机 TLS 通过，不声称外网已连通；日志显示目标、入站 / 出站要求和远端 TCP 检查命令。

## 实际验证

- 在 `services/api` 目录运行 `.venv/bin/pytest tests/test_deploy_script.py tests/test_agent_ingress_setup.py tests/test_init_env.py -q --tb=short`：C-9 修复后 **105 passed**。
- 初始新增场景 13 个失败后修复；复核中的失败应用重试、后台无确认 IP、签发失败和广播地址 4 个场景再次先失败后修复。
- `bash -n scripts/deploy.sh`、`git diff --check`、Ruff check / format、mypy 修改的测试文件：通过。
- 使用临时生成配置运行真实 `docker compose config --format json`：IPv4 / IPv6 的发布地址正确，API 收到直连 IP，HTTPS overlay 保留。没有修改本机项目 `.env`。
- 使用临时证书与真实本机 TLS 服务分别在 `127.0.0.1` 和 `::1` 调用脚本的 `check_agent_ingress`：正确主机名通过，错误主机名被拒绝。未关闭证书校验。
- `COLLAB_AGENT=codex python3 scripts/collab.py check`：通过，1 个既存的 T-0224 / T-0234 宽路径重叠警告。
- 独立只读复核完成，两项重要问题均按上述测试修复；没有改动业务接口、数据迁移、依赖或 workflow。

## C-9 的 CI 回归与修复

旧 head 的 99 项本地测试全部通过，但测试夹具暗中给了 DNS 回答，漏掉 CI 的真实默认路径。
旧 `ci-infra / agents` job 133513672779 在一键配置步骤失败：私网出站地址没有成为默认值，而且成功提示不再包含 workflow grep 的“接入入口已就绪”。这属于代码问题，不按环境瞬态重跑。

本轮先观察 6 个新场景失败，再修复：真实 ask、DEVNULL、confirm=yes、没有地址回答时复现无域名配置；另测成功提示以及私网 / 公网网卡与 --yes / 非交互的 4 种旧入口组合。
夹具现在只注入测试明确指定的回答，不再改写 ask 或默认填 `INGRESS_ADDRESS=-`。
本地 105 项通过，成功提示兼容原 CI 并明确限定本机检查。未更改 workflow；新远端 CI 在固定候选推送后验证，不能把旧 head 或本地通过冒充为新远端通过。

未在用户已卸载的服务器执行重装，也没有操作用户的 DNS / 安全组。Docker 的外网 IPv6 转发、真实两台服务器的连通性和完整生产安装，需要用户用本次分支重装时验证。

## 参考

- [实施计划](../superpowers/plans/2026-11-17-deploy-ingress-direct.md)
- [部署教程](../deploy-guide.md)
- [Cloudflare 官方端口说明源码](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/fundamentals/reference/network-ports.mdx)
