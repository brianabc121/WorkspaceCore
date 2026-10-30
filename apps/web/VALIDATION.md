# T-0016 验证记录

日期：2026-10-30（Asia/Shanghai）。目标：Sprint 2 / S2-02 ~ S2-05 前端。

- 分支从 origin/main fc11f8e 创建；Node v22.23.4，npm ci；实现前 Sprint 1 的 50 项测试通过。
- 新增 GridStack14.0.0；版本与官方来源、API、设计选择记录在 README。未修改 services、docs/TASKS、Claude 的 T-0017 路径或 schema.d.ts。
- Registry 契约测试先于模块实现；首页初次 7 项测试在旧占位页全部失败，实现后通过。包含未知类型/__proto__/constructor、越界布局、严格配置、非法链接、无 ID 导出与新建导入。
- 独立代码审查发现两项 P2，已按失败→修复→通过验证：创建组件成功但随后的 GET 失败导致重复创建；冲突后重载 GET 失败解除编辑锁。另补测导出/关闭对话框不绕过冲突锁、搜索输入自动聚焦、旧空间晚到写响应与搜索隔离。
- Vitest：82 项测试（8 个文件）；包括原 50 项回归、四种卡片的独立状态、局部失败、401/403/429、CSRF、版本冲突、导入/导出、键盘编辑与空间隔离。
- Chromium 真实鼠标（API mock）：拖拽与调整大小 → 提交全组件布局/版本 → 刷新布局保留；请求携带 CSRF 与空间头；Ctrl+K 搜索；390px 单列暗色，无 page error / 横向溢出。脚本 `tests/browser-dashboard.cjs`，截图 `docs/screenshots/`。
- lint / typecheck / test / build / check:api 在 Node22 下执行，最终实际输出写入 PR。

没有后端或数据库迁移。浏览器冒烟使用 mock，真实 Compose/后端、浏览器 E2E 纳入 CI 与 Sprint 2 总体验收归 T-0017，本任务不宣称这些已完成。安装仍有既有 whatwg-encoding / ESLint9 弃用提示；没有新增对应兼容性变更。

---

# T-0012 验证记录

日期：2026-10-30（Asia/Shanghai）。目标：Sprint 1 前端，S1-02 / S1-03 / S1-04 / S1-05 的界面部分。

## 已执行

- Node.js v22.23.4；使用隔离工具链 `/tmp/t0012-tools/node_modules/node/bin`，`npm ci` 成功；原有 18 项测试在实现前全部通过。
- `npm run generate:api`：由后端 OpenAPI 生成 TypeScript paths / schemas，无手抄 API 类型；CI 增加生成一致性检查。
- `npm run lint`：通过，0 warnings；Node globals 仅限配置与测试。
- `npm run typecheck`：通过。
- `npm run test`：50/50 通过（5 个文件）。覆盖启动次序、首次安装、登录守卫与返回地址、CSRF 换发、工作空间上下文、401/403/429、网络超时与写入结果未知、表单校验与重复提交、资料与空间保存、密码、会话撤销、审计游标与切换、空列表/失败重试、键盘焦点与原主题/导航。
- `npm run build`：通过；134 modules；JS 约 258 kB / gzip 86 kB，CSS 约 11 kB / gzip 3 kB。
- Chromium 实际浏览器（API mock）验证桌面与 390px 移动视口：受保护路由跳转登录 → 登录回到设置 → 保存资料携带 CSRF → 会话确认与 Escape → 移动导航到审计空状态。没有 page error，没有横向溢出。截图在本次运行的 `/tmp/t0012-desktop.png`、`/tmp/t0012-mobile.png`。
- Vite 深层路由 `/settings/security` 实际 HTTP 响应 200。

## 回归过程与审查

客户端与认证测试先于模块编写，初次因缺少对应模块失败；9 项页面测试先在占位界面运行，全部失败，实现后通过。

补充并发与权限回归先复现：登出后旧资料响应恢复用户、旧请求的 401 清空新会话、确认对话框 Shift+Tab 不循环、审计刷新 403 后仍显示旧记录。分别加入会话版本隔离、焦点循环、失权数据隐藏后，全套测试通过。工作空间切换测试确认请求头与结果不混用。

独立代码审查发现改密码换发 Cookie 时也必须递增会话版本；回归测试先复现旧请求晚到的 401 清空换发后的登录状态，修复为 auth store 内的 changePassword 动作先递增版本、再刷新会话列表。全套 50 项测试通过。

构建曾发现登录/安装表单联合 schema 的类型推断缺少安装字段，以及测试 API 的无效 exact 选项；已显式使用生成的 SetupRequest 类型并删除无效选项，再构建通过。

## 范围与限制

没有修改后端、数据库或平台配置，没有迁移和真实密钥。浏览器与 Vitest 使用 API mock；真实后端联调、Compose、迁移及 Sprint 1 总体验收由 T-0013 执行。本记录不宣称这些未运行项目通过，也不宣称远程 CI 已通过。

安装仍有原依赖的 whatwg-encoding 和 ESLint 9 弃用提示；本任务保留现有兼容组合。PR #7 的非阻塞建议已处理：删除旧 PR_DESCRIPTION / IMPLEMENTATION_PLAN，setup-node v5，限制 globals.node。
