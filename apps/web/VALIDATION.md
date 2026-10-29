# T-0004 验证记录

日期：2026-10-30（Asia/Shanghai）。目标：Sprint 0 / S0-02 前端骨架。

## 已执行

- Node.js `v22.23.4`（隔离工具链）、npm；`npm ci`：成功，按锁文件安装。
- `npm run lint`：成功，0 warnings。
- `npm run typecheck`：成功。
- `npm test`：18/18 通过，包括全部 12 个页面、导航跳转/收起、404、亮暗主题切换/恢复、存储不可用、Escape 与焦点恢复。
- 测试先于功能实现：初次 17 项均因缺少布局/控件失败；实现后通过。Escape 回归测试先失败（aria-expanded 仍为 true），修复后全套 18 项通过。
- `npm run build`：成功；91 modules；JS 约 131 kB / gzip 48 kB，CSS 约 9 kB。
- Vite 开发服务器实际启动；HTTP 请求 `/`、`/tasks`、`/settings/security`、`/src/main.ts` 均返回 200；深层路由返回 index.html。检查后已关闭本次服务。
- `python3 scripts/collab.py check`：通过，0 个警告。
- `git diff --check`：通过。

## 审查与限制

独立代码审查指出小屏导航关闭后仍可聚焦，已在小屏关闭状态使用 `visibility: hidden`，展开时恢复；补充 Escape 关闭与按钮焦点恢复。未运行真实浏览器的视觉或辅助技术测试；jsdom 行为测试不等于移动设备验收。

安装有两项上游弃用提示：jsdom 的传递依赖 whatwg-encoding，以及 ESLint 9。保留当前兼容组合并在版本 ADR 中记录；后续应单独安排 ESLint 主版本升级。

本任务未改动后端/Compose/数据库，无业务集成和密钥。全栈 make up、迁移、端到端联调属于 T-0006，本记录不宣称 Sprint 0 整体验收完成。GitHub Actions workflow 已配置，本地检查通过不等于远程 CI 已运行。
