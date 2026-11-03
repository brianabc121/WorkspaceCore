---
id: T-0087
title: 部署教程补全 OpenClaw 的部署步骤（第 8.6 节）
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: docs
depends_on:
created: 2026-11-03
updated: 2026-11-03
---

# T-0087 部署教程补全 OpenClaw 的部署步骤（第 8.6 节）

## 目标
人类问（2026-11-03）：「怎么部署 openclaw，在教程里没有显示」。部署教程 `docs/deploy-guide.md` 第 8.6 节只有 6 行要点，
具体做法分散在手册 `docs/deployment.md` 第 16 ~ 18 节和接入包里的 `CHECKLIST.md`。把它写成能照着做的分步教程。

## 验收标准
- [x] 第 8.6 节说明 OpenClaw 装在另一台服务器、主动连回工作台，以及两种网络模式怎么选
- [x] 分步：工作台服务器（OpenIM、入口证书与变量、`make agents-up`、防火墙）→ 工作台网页（密钥库、登记实例、密钥授权、下载接入包）→ OpenClaw 服务器（解压与权限、插件、模型、启动、定时任务）→ 确认 → 日常维护
- [x] 命令、文件名、页面上的按钮名与代码一致（接入包文件、`CHECKLIST.md`、Makefile、实例页）
- [x] `python3 scripts/collab.py check` 通过

## 背景 / 约束 / 参考
- 只改文档；接入包里的 `CHECKLIST.md` 仍是 OpenClaw 服务器的权威清单，教程与它一致并指向它。
- 不写无法核实的第三方命令（例如其他模型服务商的配置写法），只说明要改哪里。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-03 01:43 UTC · claude · 创建任务（owner: unassigned）
- 2026-11-03 01:43 UTC · human · owner unassigned → claude：人类在对话中指派：怎么部署 openclaw，在教程里没有显示
- 2026-11-03 01:43 UTC · claude · 开始任务
- 2026-11-03 01:45 UTC · claude · 教程第 8.6 节重写为分步教程：两台服务器的关系与两种网络模式的选择 → ① OpenIM（deploy.sh connect）→ ② 入口证书、.env 示例、make agents-up、防火墙 → ③ 密钥库、登记实例、密钥授权（ref_id openai_api_key）、下载接入包 → ④ OpenClaw 服务器：解压与权限、插件（sha256 与安装命令同 CHECKLIST.md）、模型、启动、cron → ⑤ 确认 → 日常维护。按钮名、文件名、命令都对照了代码（实例页、密钥库页、agent_bundle、Makefile）。
- 2026-11-03 01:45 UTC · claude · status in_progress → done：部署教程补全 OpenClaw 部署步骤
