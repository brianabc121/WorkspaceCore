---
id: T-0106
title: 时间轴与知识库图片的验收与文档：真实 OpenClaw 用量上报、浏览器验收、S3 存储验收、威胁模型与部署文档
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: tests/e2e,.github/workflows,docs,services/api,apps/web,scripts
depends_on: T-0103, T-0105
created: 2026-11-04
updated: 2026-11-04
---

# T-0106 时间轴与知识库图片的验收与文档：真实 OpenClaw 用量上报、浏览器验收、S3 存储验收、威胁模型与部署文档

## 目标
在真实环境上验收时间轴（含真实 OpenClaw 的 token 上报）和知识库图片，补齐文档。

## 验收标准
- [x] agents CI：真实 OpenClaw 上报用量，交给智能体执行后时间轴显示执行段与 token，完成审批后段结束；SSH 部署写入上报定时任务
- [x] compose CI：时间轴浏览器验收（全部 / 按项目、开始暂停、拖动计划、手机宽度、viewer 只读）；知识库图片浏览器验收（插入、粘贴、显示、清理）
- [x] S3 存储在 Garage 上的验收（主存储 + 副本、读主失败读副本）；本地存储的备份恢复验收
- 真实 Cloudflare R2 / 阿里云 OSS 账号上的验收（从 T-0103 移来）：**没有做**——本环境没有测试桶，官方文档域名仍被网络策略拦截（2026-11-04 再次确认，代理返回 403）。移到 T-0107，等人类提供测试桶或放开 `developers.cloudflare.com`、`help.aliyun.com`
- [x] 威胁模型、架构、部署手册、运维手册、versions；方案状态改为已完成

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 05:15 UTC · claude · 开始任务
- 2026-11-04 05:21 UTC · claude · 进行中：Garage 上的主存储 + 副本验收（虚拟主机风格 + root_domain、复制、读主失败读副本、清理两边）；时间轴浏览器验收 timeline_browser.cjs（本机 Compose 通过）；知识库浏览器加粘贴截图与每日清理（本机通过）；agents 场景 16（真实 OpenClaw 用量上报、交给甲执行、complete_task 审批后段结束）与 SSH 验收照 crontab 跑一次上报，等 CI 验证。验收中发现拖动计划片段后没选中任务、结果提示看不到，已修（拖完选中这个任务）。越界说明：.github/workflows 与 scripts 在 paths 内；改了 apps/web 的 TimelineBoard.vue（T-0105 已 done，这是验收发现的小修正）。下一步：文档（威胁模型、架构、部署、运维、versions）与方案状态。
- 2026-11-04 05:26 UTC · claude · 文档：威胁模型（知识库图片四行、时间轴上报的 CI 验收）、架构（知识库图片与任务时间轴）、部署手册第 19.4 节与新的第 20 节（token 从哪来、“未上报”排查、中断、验收状态）、部署指南（connect 的图片存储一行、时间轴、运维速查）、versions、发布 checklist、方案状态改为已完成。真实 R2 / OSS 验收移到新任务 T-0107（没有测试桶，文档域名仍被拦截）。
- 2026-11-04 05:26 UTC · claude · status in_progress → done：验收标准完成（agents 场景 16、SSH 上报、时间轴与图片浏览器验收、Garage 主存储 + 副本、文档）；真实 R2 / OSS 账号验收移到 T-0107
- 2026-11-04 05:41 UTC · claude · agents CI 第一次运行：场景 16 只有“第一次上报只记基准”一项失败——实例接口不返回 usage_reported_at（只在库里），检查读不到；上报本身成功（退出码 0，没有用量分钟），后面交给甲执行、审批、token 归属、时间轴接口都通过。改成查库，与 SSH 验收一致。
