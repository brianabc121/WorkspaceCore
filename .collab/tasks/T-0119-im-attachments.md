---
id: T-0119
title: 附件：图片 / 文件 / 视频的四种来源与检查、上传与消息元素、只放行工作台签名的附件、媒体接口与网页显示
owner: claude
status: done
priority: P1
branch: claude/cool-wright-99qbbg
paths: services/api,apps/web,docs,tests/e2e
depends_on: T-0118
created: 2026-11-04
updated: 2026-11-06
---

# T-0119 附件：图片 / 文件 / 视频的四种来源与检查、上传与消息元素、只放行工作台签名的附件、媒体接口与网页显示

## 目标
智能体能发图片、文件、视频，附件经过工作台检查（方案第 4.2 ~ 4.6、6.4 节）。

## 验收标准
- [x] 四种来源：公网网址、内联、文字成文件、工作台引用；识别文件头、SSRF、密钥扫描、禁止类型、大小与限额
- [x] 上传到 OpenIM 对象存储；图片 / 文件 / 视频三种元素（视频时长与默认封面）
- [x] 回调只放行带工作台签名的附件消息（5007）
- [x] 媒体接口扩展到文件与视频（实例经接入入口、管理员经网页接口，Range）
- [x] 管理员 IM 页显示与发送文件、视频；实例的附件开关
- [x] 后端与前端检查通过

## 背景 / 约束 / 参考
- 方案：`docs/v2/AGENT_MESSAGING_PLAN.md`（人类 2026-11-04 批准，D1 ~ D19 按推荐）

## 进度日志
<!-- 只追加不改写；scripts/collab.py 会自动写入。格式：- 时间 · 身份 · 内容 -->
- 2026-11-04 19:02 UTC · claude · 创建任务（owner: claude）
- 2026-11-06 07:25 UTC · claude · 开始任务
- 2026-11-06 07:25 UTC · claude · 迁移 0035（im_media 加 kind / file_name / duration_seconds / sha256 / instance_id，放宽尺寸约束；agent_instances 加 im_media_enabled / by / at）；app/domain/agent_media.py：四种来源（公网网址只取公网地址、每次跳转重查、10 秒、超限断开；内联 base64；文字成文件；im_media 引用只限本工作空间）、按文件头识别、挡可执行与网页类、文字附件密钥扫描、图片 10 MB / 文件 20 MB / 视频 50 MB、每小时 30 个、每天 200 MB；上传到 OpenIM 对象存储，图片 102 / 文件 105 / 视频 104（读 mvhd 时长、默认 320x180 封面）；ex 带 HMAC 签名（myws_sig），before 回调对没签名或签名不对的附件消息拒收 5007；媒体接口文件按附件下载、视频支持 Range（管理员 /api/v1/im/media 与实例 /im/obj/m）；管理员 IM 页显示、下载文件，点播放才取视频，可上传发送文件；实例卡片“附件权限”开关（PUT /agents/instances/{id}/im-media，乐观并发 + 审计）；Caddy 只给 /api/v1/im/uploads 放宽到 51 MB。顺带修了 ImMessageSend：文件、视频也必须只带 upload_id。tests/test_agent_media.py 6 个用例、im.test.ts / agents.test.ts 新增 6 个用例通过。
- 2026-11-06 07:25 UTC · claude · status in_progress → done：附件发送完成；真实 OpenClaw 验收在 T-0125
- 2026-11-06 07:48 UTC · claude · 补记：infra/caddy/routes.caddy 不在任务 paths 内，为了让 /api/v1/im/uploads 能上传 50 MB 的视频改了一行请求体上限（其他路径仍是 10 MB）；没有碰别人进行中的任务。
- 2026-11-06 17:07 UTC · claude · PR #163 的 agents 场景 24 在 9a10ebc 上失败：管理员下载文件请求的是 /api/v1/im/media/None。根因：parse_message 只保留文字、@、图片的 content，文件（105）、视频（104）存成 {}；对账把 OpenIM 的消息补进投影时 ON CONFLICT 用它覆盖了发件箱先写好的内容，附件的 media_id、文件名都没了（管理员网页和 im_read_messages 都受影响）。之前能通过是因为验收在对账之前就读完了。修复：投影保留文件、视频显示与下载要用的字段；加了回归测试（对账后文件、视频仍有 media_id、能下载），改前失败、改后通过。
