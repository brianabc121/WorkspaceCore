---
id: T-0274
title: 一键部署脚本直接部署 OpenIM / TrendRadar / Prometheus / Grafana，不再询问
owner: claude
status: done
priority: P2
branch: claude/amazing-faraday-6ey29e
paths: scripts/deploy.sh,Makefile,services/api/tests/test_deploy_script.py,docs/deploy-guide.md,docs/deployment.md
depends_on:
created: 2026-11-16
updated: 2026-11-16
---

# T-0274 一键部署脚本直接部署 OpenIM / TrendRadar / Prometheus / Grafana，不再询问

## 目标
人类 2026-11-16 在对话里要求：“在一键部署脚本，直接部署 OpenIM/TrendRadar/Grafana/Prometheus 不需要再次询问”。
原来 OpenIM、新闻早报（TrendRadar）在前台问“部署？”（内存、磁盘不够时默认“否”）；监控（Prometheus + Grafana）根本不在脚本里，要按文档手动起。

## 验收标准
- [x] install / connect：OpenIM、新闻早报不再询问，直接部署；内存、Docker 所在磁盘偏小时只提示（OpenIM 构建失败时跳过、不影响其他步骤，和原来一样）
- [x] 以前的回答文件里写着不部署（`OPENIM=n`、`NEWS=n`）也照样部署；确认汇总里不再列“部署 OpenIM”
- [x] 新增监控一步：`make monitoring-pull`、`make monitoring-up`（`--profile monitoring` 只起 prometheus、grafana，等健康检查），`.env` 里三个地址为空时填上（`GRAFANA_DASHBOARD_URL` 跟着 `GRAFANA_PUBLIC_URL`），有变化才重启 api、worker；手动改过的地址不覆盖；已部署时跳过并提示 Grafana 的访问方式
- [x] 升级：监控在运行时按新版本锁定的镜像重建（`make up` 不碰 profile 服务）
- [x] `status`、菜单、接入汇总都显示新闻早报与监控的状态
- [x] 部署脚本测试、shellcheck（-S warning 无告警）通过；本机真实 Docker 上跑通 `make monitoring-up` 与脚本的 `connect_monitoring`
- [x] 文档：deploy-guide 第 1 段与第 8.4 节、deployment 第 12、17、23 节

## 背景 / 约束 / 参考
- 编号由登记的集成会话在 #235 预留：https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6929159380
- AGENTS.md 第 8 节“核心 Compose 优先，AFFiNE/Monitoring 可选部署”：监控仍是 Compose 的可选 profile（`make up` 不启动它），只是一键脚本默认把它部署上；
  这是人类本次的明确要求。Grafana 端口仍只绑定本机，管理员密码由 `make env` 随机生成。
- `scripts/deploy.sh` 刚由 T-0270（codex，done）改过接入入口；本任务只改可选组件的提问与新增监控，不动接入入口。
- 与 T-0271、T-0273 同一个分支 / PR（#241）。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-16 21:10 UTC · claude · 创建任务（owner: unassigned；编号 T-0274 由集成会话在 #235 预留）
- 2026-11-16 21:09 UTC · human · owner unassigned → claude：人类在对话中指派：一键部署脚本直接部署 OpenIM / TrendRadar / Grafana / Prometheus，不再询问
- 2026-11-16 21:09 UTC · claude · 开始任务
- 2026-11-16 21:16 UTC · claude · 做完：deploy.sh 的 OpenIM、新闻早报去掉 decide（前台 plan_components 只问接入入口，后台 connect_openim / connect_news 直接部署，以前回答文件里的 OPENIM=n / NEWS=n 不再看）；OpenIM 的内存、磁盘提醒抽成 openim_resource_notes，只提示不拦；新增监控一步（plan_monitoring、connect_monitoring、monitoring_env、monitoring_hint、upgrade_monitoring），接入、升级、status、菜单、汇总都带上；Makefile 加 monitoring-pull / monitoring-up / monitoring-down / monitoring-logs。测试：test_deploy_script 改了 plan_components 与拉取顺序两处，新增 5 个（直接部署不提问且无视旧的“否”、监控部署并写 .env、手动地址不覆盖与已部署跳过、升级只重建运行中的监控、install / connect / upgrade 都包含监控），共 78 个通过；shellcheck -S warning 无告警。本机自己的 dockerd 上真实跑通 make monitoring-pull / monitoring-up（prometheus、grafana 都 Healthy）与脚本的 connect_monitoring（第一次部署并写三个地址，第二次识别为已部署），之后删除了测试用的容器、数据卷和 .env。文档：deploy-guide 第 1 段与 8.4、deployment 第 12 / 17 / 23 节。
- 2026-11-16 21:16 UTC · claude · status in_progress → done：一键部署脚本直接部署 OpenIM / TrendRadar / Prometheus / Grafana、不再询问，验收标准全部勾选
