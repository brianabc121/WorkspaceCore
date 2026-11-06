---
id: T-0137
title: 运行服务器 R2 模板：保存、构建、版本、runner_templates / runner_template_save
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,docs
depends_on: T-0136
created: 2026-11-06
updated: 2026-11-06
---

# T-0137 运行服务器 R2 模板：保存、构建、版本、runner_templates / runner_template_save

## 目标
智能体与管理员都能直接定义模板（方案第 3、10 节，D1、D9）。

## 验收标准
- [x] 模板的保存（版本号加 1）、在运行服务器上构建或拉取、镜像 digest、构建耗时与大小
- [x] runner_templates / runner_template_save 工具；只能改自己建的

## 背景 / 约束 / 参考
- 方案：`docs/v2/RUNNER_PLAN.md`（人类 2026-11-06 批准：D1 一个运行环境权限、D2 runner-agent、D11 不预设场景，其余按推荐）
- 规则：`docs/AGENTS.md` 第 8 节例外八

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-06 22:14 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 22:15 UTC · claude · 人类 2026-11-06 在对话中指派：按修订后的运行服务器方案（D1、D2、D11 按人类决定，其余按推荐）全部落实。
- 2026-11-06 23:07 UTC · claude · 开始任务
- 2026-11-06 23:08 UTC · claude · 模板：保存（规格变了版本号加 1）、下发 build_template（无安装步骤就拉取并记 digest，有步骤就用经典构建器在只连出口代理的 internal 网络上构建，拒绝带网址的 ADD 与私有地址的仓库），记录 digest、耗时、大小、日志；runner_templates / runner_template_save 工具，只能改自己建的；管理员接口新建、修改、停用、重新构建。集成测试 tests/test_runner_api.py::test_templates_build_on_the_runner，本地真实构建见核验记录第 4 节。
- 2026-11-06 23:08 UTC · claude · status in_progress → done：模板保存、构建与工具完成（PR-A）；页面上的模板标签随 T-0140
