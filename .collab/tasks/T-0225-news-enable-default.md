---
id: T-0225
title: 一键部署脚本部署好 TrendRadar 后默认打开“启用新闻早报”
owner: claude
status: done
priority: P2
branch: b6-model-connections
paths: services/api/app/modules/news/service.py,services/api/app/commands/connect.py,services/api/tests/test_news_enable_default.py,services/api/tests/test_deploy_script.py,scripts/deploy.sh,docs/deployment.md,docs/deploy-guide.md,docs/v2/NEWS_PLAN.md
depends_on:
created: 2026-11-13
updated: 2026-11-13
---

# T-0225 一键部署脚本部署好 TrendRadar 后默认打开“启用新闻早报”

## 目标
人类 2026-11-13 要求：“新闻早报的设置页面，一键部署脚本在部署好 TrendRadar 后默认开启‘启用新闻早报’的开关”。
原来部署好 TrendRadar 后开关是关着的，要用户自己到网页上打开才开始抓取。

## 验收标准
- [x] 后端命令 `connect news-enable`：以首次安装创建的管理员（owner）身份，在他的工作空间里按默认设置打开早报，
      与网页保存走同一条路（校验、生成 TrendRadar 配置文件、审计）
- [x] 只在这个工作空间还没保存过早报设置、也没有别的工作空间打开时打开；保存过的（包括关掉的）不改；
      模块停用、没部署 TrendRadar 时不打开；非 owner 拒绝
- [x] `deploy.sh` 部署好 TrendRadar 后（包括已经部署过、重新运行 connect）调用它并报告结果；失败只提示、不中断部署
- [x] 测试：真实数据库的集成测试 + 部署脚本的行为测试；文档（部署手册第 23 节、部署指南、NEWS_PLAN）更新

## 背景 / 约束 / 参考
- 一个部署同一时刻只能有一个工作空间打开早报（数据库唯一约束），所以只打开管理员自己的工作空间，不抢占别人的。
- 路径与 codex 进行中的 T-0224（声明了整个 services/api、docs/decisions 等）有名义上的重叠：本任务只改新闻模块的服务、
  connect 命令、部署脚本与对应测试 / 文档，codex 分支（codex/T-0222-runtime-v2-placement-network）在主干之外没有改动这些文件。

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-13 04:42 UTC · claude · 创建任务（owner: claude）
- 2026-11-13 04:42 UTC · claude · 开始任务
- 2026-11-13 04:49 UTC · claude · 实现完成：NewsService.enable_by_default + connect news-enable + deploy.sh news_enable（两条分支都调用）；本地 ruff、mypy 522、相关 90 个测试（新闻、connect、部署脚本）通过
- 2026-11-13 04:49 UTC · claude · status in_progress → done：部署好 TrendRadar 后默认打开新闻早报（只在还没保存过设置时）
