---
id: T-0107
title: 真实 Cloudflare R2 / 阿里云 OSS 账号上的知识库图片存储验收（等测试桶）
owner: claude
status: todo
priority: P2
branch:
paths: services/api,docs,scripts
depends_on: T-0106
created: 2026-11-04
updated: 2026-11-04
---

# T-0107 真实 Cloudflare R2 / 阿里云 OSS 账号上的知识库图片存储验收（等测试桶）

## 目标
知识库图片的 R2 / OSS 存储目前只在 S3 兼容的 Garage 上验收过（路径与虚拟主机风格、主存储 + 副本），默认的 endpoint、region、
寻址方式来自官方文档的搜索摘要（官方文档域名被本环境的网络策略拦截）。在真实账号上确认一遍，再把方案第 5 节的“没有验收”去掉。

## 验收标准
- [ ] 人类提供 R2 与 OSS 各一个私有测试桶和只能读写该桶的密钥（经环境的密钥设置，不写进仓库、不贴在聊天里），或在环境设置里放开 `developers.cloudflare.com`、`help.aliyun.com`
- [ ] 两个桶分别做主存储与副本：`deploy.sh connect` 的“知识库图片存储”一步写入 `.env`，运维页自检通过（写、读、删）
- [ ] 上传、阅读页显示、副本复制、主存储不可用时读副本、清理两边都删；结果记进 `docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md` 第 3 节
- [ ] 能读到官方文档时，核对 R2（`auto` region、路径风格、辖区 endpoint）与 OSS（`s3.oss-<地域>.aliyuncs.com`、虚拟主机风格）的默认值，有出入就改代码与文档

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md` 第 5 节；部署：`docs/deployment.md` 第 19 节
- 从 T-0103 移到 T-0106、再从 T-0106 移到这里：本环境没有测试桶，官方文档域名被网络策略拦截（2026-11-04 再次确认，代理返回 403）
- 在拿到测试桶之前不开工

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 05:25 UTC · claude · 创建任务（owner: claude）
