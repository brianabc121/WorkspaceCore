# ADR：Sprint 0 前端技术栈

- 状态：已采纳
- 日期：2026-10-30
- 任务：T-0004 / S0-02

## 决策与依据

沿用已采纳的 Sprint 0 接口契约，不重新选择框架。Node.js 22 LTS、npm、Vue Router history 路由；所有依赖使用精确版本，传递依赖由 package-lock.json 锁定。当前仅布局、导航、主题和占位页，不实现业务接口。

Tailwind 4 使用官方 Vite 插件；所有界面颜色集中为 CSS 主题变量。Vue Query 在应用入口注册，尚无业务请求。组件测试使用真实 Router、Pinia 和 Vue Query provider，jsdom 仅补齐不支持的 scrollTo。

ESLint 9 用于兼容当前 typescript-eslint 和 eslint-plugin-vue 的 flat config；安装提示该主版本已停止支持，后续升级到新主版本应单独核对插件兼容性。未在本任务引入表单组件、shadcn-vue 或拖拽依赖，待对应 Sprint 使用时再核验。

## 锁定版本

| 包 | 版本 |
| --- | --- |
| @tanstack/vue-query | 5.105.0 |
| pinia | 3.0.4 |
| vue | 3.5.44 |
| vue-router | 4.6.4 |
| @eslint/js | 9.39.5 |
| @tailwindcss/vite | 4.3.3 |
| @testing-library/jest-dom | 6.9.1 |
| @testing-library/vue | 8.1.0 |
| @types/node | 22.20.5 |
| @vitejs/plugin-vue | 6.0.10 |
| eslint | 9.39.5 |
| eslint-plugin-vue | 10.12.0 |
| globals | 16.5.0 |
| jsdom | 26.1.0 |
| tailwindcss | 4.3.3 |
| typescript | 5.9.3 |
| typescript-eslint | 8.72.1 |
| vite | 7.3.7 |
| vitest | 3.2.7 |
| vue-tsc | 3.3.14 |

## 官方来源

- Vue: https://vuejs.org/guide/quick-start.html
- Vue Router history: https://router.vuejs.org/guide/essentials/history-mode.html
- Vite / Node 要求: https://vite.dev/guide/
- Pinia: https://pinia.vuejs.org/
- Vue Query: https://tanstack.com/query/latest/docs/framework/vue/overview
- Tailwind Vite 插件: https://tailwindcss.com/docs/installation/using-vite
- Vitest: https://vitest.dev/guide/

版本以本次 npm registry 实际解析结果和提交的锁文件为准；以上不构成第三方业务 API 已验证的声明。

## Sprint 1 新增依赖（T-0012 引入，T-0013 由 claude 补录核验结果）

T-0012（codex，PR #16）因任务路径限制把依赖来源写在 `apps/web/README.md`；按 D-0003“版本核验 ADR 由 claude 负责”，在此补录。

| 包 | 版本 | 类型 | 用途 |
| --- | --- | --- | --- |
| openapi-typescript | 7.13.0 | dev | 由 `services/api/openapi.json` 生成 `src/services/schema.d.ts`（`npm run generate:api`；CI 用 `npm run check:api` 检查一致） |
| openapi-fetch | 0.17.0 | runtime | 基于生成类型的强类型 `fetch` 客户端 |
| vee-validate | 4.15.1 | runtime | 表单状态与校验 |
| @vee-validate/zod | 4.15.1 | runtime | vee-validate 的 Zod 适配器 |
| zod | 3.25.76 | runtime | 表单校验 schema（适配器支持的 Zod 3 版本） |

核验：2026-10-30 用 `npm view <包>@<版本> version` 在 npm registry 确认上述版本均存在，锁文件 `apps/web/package-lock.json` 固定传递依赖。
官方文档（codex 引用；本次验收环境的网络策略未放行这些站点，未重新打开）：
https://openapi-ts.dev/introduction 、 https://openapi-ts.dev/openapi-fetch/ 、 https://vee-validate.logaretm.com/v4/integrations/zod-schema-validation/ 。

后果：`check:api` 让 `services/api/openapi.json` 与前端生成类型强绑定——后端修改接口契约的 PR 必须同时提交重新生成的
`apps/web/src/services/schema.d.ts`，否则 Web CI 失败。这个生成文件由谁提交需要在协作流程里明确（见 `docs/acceptance/sprint-1.md` 第 6 节）。

## Sprint 2 新增依赖：拖拽网格（T-0016 引入，T-0017 由 claude 补录核验结果）

规范第 2 节把“拖拽布局”定为“GridStack 或维护活跃的 Vue 网格库，Sprint 2 评估后固定”。T-0016（codex，PR #23）选定 GridStack，并把来源写在 `apps/web/README.md`；按 D-0003 在此补录。

| 包 | 版本 | 类型 | 用途 |
| --- | --- | --- | --- |
| gridstack | 14.0.0 | runtime | 桌面端 12 列网格的拖拽与调整大小（`float` 模式保持存储的坐标；DOM 和组件内容由 Vue 渲染，不向 GridStack 传入 HTML） |

核验（2026-10-30，npm registry `https://registry.npmjs.org/gridstack`）：
- 14.0.0 发布于 2026-09-21，是当前 `latest`；MIT 许可；没有运行时依赖（`dependencies` 为空），不引入传递依赖。
- 包内自带类型（`dist/gridstack.d.ts`），前端用到的 `GridStack.init`、`load(items, addRemove)`、`setStatic`、`on('change')`、`destroy(removeDOM)`、`engine.nodes` 都在该版本的类型里。
- `apps/web/package-lock.json` 里的 integrity 与 registry 一致，`npm ci` 时校验通过。
- 官方站点 https://gridstackjs.com/ 与 GitHub 发布页 https://github.com/gridstack/gridstack.js/releases 在本次验收环境的网络策略下被拒绝（403），未重新打开；以 registry 元数据与包内类型为准。

结论：Sprint 2 起，拖拽网格固定为 GridStack 14.0.0；升级主版本时需要重新核验上述 API，并跑 `tests/e2e/sprint2_browser.cjs`（拖拽、调整大小、保存后刷新）。
