---
id: T-0103
title: 知识库图片：本地 / R2 / OSS 存储与副本、上传读取、编辑器与图谱、清理与备份（迁移 0026）
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,scripts,compose.yaml,.env.example,tests/e2e,.github/workflows
depends_on: T-0102
created: 2026-11-04
updated: 2026-11-04
---

# T-0103 知识库图片：本地 / R2 / OSS 存储与副本、上传读取、编辑器与图谱、清理与备份（迁移 0026）

## 目标
按方案第 4 节：知识库笔记支持图片（插入、粘贴、拖入），存到本地 / Cloudflare R2 / 阿里云 OSS（主存储 + 可选副本，都没配置就本地），图片经工作台读取；图谱显示缩略图与可选的图片节点；引用、清理、配额、备份。

## 验收标准
- [x] 上传：PNG / JPEG / GIF / WebP（按文件头判断，拒绝 SVG），单张 10 MB，工作空间配额，JPEG 去 EXIF，同内容去重；member 以上可上传，viewer 以上可读，跨工作空间 404
- [x] 存储：本地卷；S3 兼容客户端（自写 SigV4，超时、错误分类、最多重试 3 次，PUT 幂等、结果不确定时 HEAD 核对）；R2 与 OSS 的默认 endpoint / region / 寻址方式；副本由 Worker 异步复制、失败重试，读主存储失败读副本；部署自检
- [x] 编辑器：工具栏插入、粘贴、拖入，上传占位与失败提示；阅读页显示并可放大；图谱侧栏缩略图，“显示图片”节点
- [x] 引用表与每日清理（无引用且超过 24 小时）；本地存储纳入 `make backup` 与 `make restore-verify`
- [x] `deploy.sh connect` 增加图片存储一步；运维页显示存储状态与用量；`.env.example`、部署文档
- [x] 迁移 0026 与测试；SigV4 用 AWS 官方测试向量；在 Garage 上验证路径风格读写删
- 真实 R2 / OSS 账号上的验收：本环境没有测试桶，官方文档域名也被网络策略拦截，**没有做**；移到 T-0106 的验收标准，等人类提供测试桶（或放开 `developers.cloudflare.com`、`help.aliyun.com`）后补做

## 背景 / 约束 / 参考
- 方案：`docs/v2/TIMELINE_PLAN.md`（人类 2026-11-04 批准：“之后按照推荐的来，给我全面落实”）
- 核验：`docs/decisions/2026-11-04-timeline-kb-upgrade-verification.md`
- 效果图：https://claude.ai/artifact/CHuDA9DsafbAudwxB6A61y

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 01:56 UTC · claude · 创建任务（owner: claude）
- 2026-11-04 02:55 UTC · claude · 开始任务
- 2026-11-04 03:20 UTC · claude · 越界说明：除 paths 外改了 tests/e2e/backup_acceptance.py、tests/e2e/e2e_support.py（A15 加图片的备份恢复检查）与 tests/e2e/knowledge_browser.cjs（图片浏览器验收），没有别人进行中的任务。读取接口不要求 X-Workspace-Id（<img> 带不了请求头），按图片所属工作空间校验，方案 4.2 已注明。真实 R2 / OSS 账号验收移到 T-0106（没有测试桶，文档域名被拦截）。
- 2026-11-04 03:20 UTC · claude · status in_progress → done：完成：本地 / R2 / OSS 主存储 + 副本（自写 SigV4，AWS 测试向量一致，Garage 上路径风格读写删通过），上传 / 读取接口（类型按文件头、大小、配额、JPEG 去元数据、去重、按所属工作空间校验），引用与每日清理、副本复制、自检；编辑器插入 / 粘贴 / 拖入、阅读页放大、图谱缩略图与图片节点、运维页存储面板；卷 kb-files、备份恢复校验、deploy.sh connect 图片存储一步、.env.example、部署文档。检查：pytest 694 passed 13 skipped，Garage 3 passed；web 401 测试、lint、typecheck、build、check:api；本地浏览器上传→显示→放大→图谱→运维自检，亮暗与手机无错误无溢出。
