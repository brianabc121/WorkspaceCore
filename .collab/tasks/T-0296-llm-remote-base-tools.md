---
id: T-0296
title: 大模型客户端：远程开发容器的派生镜像自动补装基础命令
owner: claude
status: done
priority: P2
branch: claude/cool-wright-99qbbg
paths: services/api/app/modules/llm_bridge,services/api/tests/test_llm_bridge_remote.py,services/api/openapi.json,apps/web/src/modules/llm_bridge,apps/web/src/services/schema.d.ts,docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md
depends_on: T-0295
created: 2026-11-18
updated: 2026-11-18
---

# T-0296 大模型客户端：远程开发容器的派生镜像自动补装基础命令

## 目标
远程开发容器的托管进程外壳（`wc`、`tail`、`head`、`mv`、`mkfifo`、`setsid`）、辅助命令（`timeout`、`date`）、文件浏览（`realpath`、`stat`、`base64`、`find -printf`）都依赖 coreutils、findutils、util-linux。内置镜像里都有，但“自定义镜像”或“模板”的基础镜像里不一定有。
#257 合并时集成会话提了这条备注；人类 2026-11-18 在对话中答复（原话）：“可以 给我安装上”。

## 验收标准
- [x] 派生模板的构建步骤先 `USER root` 跑检查脚本（`remote_config.TOOLS_SCRIPT`）：都有就什么也不做；缺了只补装缺的那几个包（coreutils / findutils / util-linux），用基础镜像自带的包管理器（apt-get / dnf / microdnf / yum / zypper / apk）
- [x] 没有包管理器、或装完还缺，构建失败并写明缺什么；不静默
- [x] 只在构建派生模板时做，不碰运行中的容器；访问镜像源走原有的构建出口代理，没有新增网络出口
- [x] 测试：假的包管理器验证“都有不装”“只装缺的包”“dnf 系只装 findutils”“没有包管理器失败”“装完还缺失败”；真实容器里 debian（不装、通过）和 busybox（没有包管理器、失败并写明原因）
- [x] 页面和接口里自定义镜像的说明、方案第 5 节和第 22 节
- [x] 工作台 ruff / mypy / 相关测试 / openapi；前端 lint / typecheck / 测试

## 背景 / 约束 / 参考
- 编号由集成会话 `session_0171QGWLzYfTCvXBmKt9H2Yq` 在 #235 预留（[预留记录](https://github.com/brianabc121/WorkspaceCore/issues/235#issuecomment-6985681038)）。
- 已有的派生镜像在下一次“更新镜像”或“重来”时按新步骤构建；内置镜像不变。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-18 17:11 UTC · claude · 创建任务（owner: claude）
- 2026-11-18 17:11 UTC · claude · 开始任务
- 2026-11-18 17:11 UTC · claude · 实现完成：remote_config.TOOLS_SCRIPT + derived_steps 先 USER root 检查并补装基础命令；页面和接口的说明、方案第 5、22 节已改。测试 test_derived_images_install_only_the_missing_base_tools（假的包管理器 5 种情况）；真实容器里 debian:bookworm-slim 不装、通过，busybox 没有包管理器、失败并写明原因。本机：ruff、mypy、test_llm_bridge_remote + test_runner_managed 16 passed、export_openapi --check；前端 lint、typecheck、llm_bridge_remote 7 passed。
- 2026-11-18 17:11 UTC · claude · status in_progress → done：验收标准全部勾选；CI 以 PR 的检查为准
