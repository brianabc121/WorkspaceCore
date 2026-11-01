# ADR：引入 shadcn-vue；右侧 AI / 详情抽屉与 Task Drawer（T-0066）；顶栏创建菜单（T-0067）

- 状态：已采纳（2026-11-01，人类在对话中要求实现）
- 关联：规范第 2 节技术栈（“Tailwind CSS + shadcn-vue”）、第 8.1 节（右侧 320 ~ 420px 可收起 AI / 详情抽屉）、第 8.4 节（新增 / 编辑 Task Drawer）；
  `2026-10-30-frontend-stack.md`（当时没有引入 shadcn-vue，留到需要时再核验）

## 1. shadcn-vue 怎么引入

shadcn-vue 不是运行时依赖：它的做法是把组件源码复制进项目，由项目自己维护。官方 CLI（`npx shadcn-vue add`）从
`https://shadcn-vue.com/r/styles/<style>/<组件>.json` 下载组件；本会话的出站代理拒绝访问 shadcn-vue.com，
所以改为从官方仓库的发布标签取同一份源码：

| 项目 | 值 |
|---|---|
| 来源 | `github.com/unovue/shadcn-vue`，标签 `v2.9.0`（提交 `39cb0b277f009e47de2dc62c4217755fb9a27eec`，2026-10-28），目录 `apps/v4/registry/new-york-v4/ui/` |
| CLI 版本核对 | npm 上 `shadcn-vue` 最新为 2.9.0；CLI 的注册表地址 `REGISTRY_URL = ${SHADCN_VUE_URL}/r`、默认风格 `new-york-v4`（从 2.9.0 的发布包核对） |
| 复制的组件 | `sheet`、`tabs`、`button`（T-0066）；`dropdown-menu` 中的 Menu、Trigger、Content、Item、Label、Separator、Group（T-0067）（`apps/web/src/components/ui/`） |
| 配置 | `apps/web/components.json`（style `new-york-v4`，别名 `@/components`、`@/lib/utils`；`vite.config.ts` 与 `tsconfig.json` 增加 `@` → `src`） |

对源码的改动（都在复制进来的文件里，便于以后对照上游）：

- `import { cn } from "cn"` 改为 `@/lib/utils`（CLI 安装时也会这样改写）；
- 不引入 `@vueuse/core`：`reactiveOmit` 换成 `src/lib/utils.ts` 里的 `omitProps`（返回 computed，reka-ui 的 `useForwardProps` 接受 ref）；
- 不引入 `@lucide/vue`（只用来画关闭按钮的 ×，而且 1.66.0 在核对时发布才一天）：改成内联 SVG；关闭按钮的读屏文字改为与项目其他对话框一致的“关闭对话框”；
- Sheet 和 DropdownMenuContent 的边框加 `border-border`（Tailwind 4 的 `border` 默认是 currentColor，项目没有给全局元素加默认边框色）；
- Button、SheetContent、DropdownMenuContent、DropdownMenuItem 补上 `class` 等可选属性的默认值（项目的 ESLint 规则 `vue/require-default-prop`）；
- dropdown-menu 的 `index.ts` 只导出复制进来的组件。

以后要加别的组件：能访问 shadcn-vue.com 时用 CLI（会读 `components.json`）；不能访问时按上面的方式从同一标签复制，并做同样的改动。

## 2. 依赖（精确版本，都已发布两周以上）

| 包 | 版本 | 发布日期 | 许可证 | 用途 |
|---|---|---|---|---|
| reka-ui | 2.11.0 | 2026-10-05 | MIT | shadcn-vue 组件的无样式基础（Dialog、Tabs 等，含焦点管理与 ARIA） |
| class-variance-authority | 0.7.1 | 2024-11-26 | Apache-2.0 | 组件的样式变体 |
| clsx | 2.1.1 | 2024-04-23 | MIT | 拼接 class |
| tailwind-merge | 3.7.0 | 2026-09-12 | MIT | 合并冲突的 Tailwind class（支持 Tailwind 4） |
| tw-animate-css（开发） | 1.4.0 | 2025-09-24 | MIT | Sheet 的进出动画（纯 CSS） |

来源：npm registry（`npm view <包> time`），2026-11-01 核对。

## 3. 主题

shadcn-vue 组件用的颜色令牌（`background`、`foreground`、`border`、`primary`、`accent` 等）在 `src/style.css` 的
`@theme inline` 里映射到项目已有的变量（`--surface`、`--text`、`--line`、`--accent`、`--accent-soft` 等），亮暗主题自动跟随，
没有新增颜色，也没有改全局元素样式。下拉菜单用的 `popover` / `popover-foreground` 同样映射到 `--surface` / `--text`。现有的页面组件（`.panel`、`.primary-button` 等）不变，以后按需逐步换成 shadcn-vue 组件。

## 4. 右侧 AI / 详情抽屉

- 顶栏“AI 助手”按钮打开 / 收起。宽屏（≥ 1280px）停靠在页面右侧，宽 380px（规范 320 ~ 420px），与页面并排；
  窄屏改为覆盖页面的侧滑面板（shadcn-vue Sheet，模态，全宽，最大 400px）。
- 是否打开记在浏览器的 localStorage（个人偏好），只用于宽屏停靠；窄屏加载页面时不自动弹出。
- 两页（shadcn-vue Tabs）：
  - **AI 助手**：V2 里 AI 就是经 IM 对话的 OpenClaw 实例。选择实例（只有一个时自动选中），显示最近 30 条对话，发消息带 Idempotency-Key；
    走工作台的 IM 接口，浏览器不直连 OpenIM；只对 owner / admin 开放（服务端校验，这里只显示说明）。
  - **详情**：页面用 `DrawerDetail` 组件把详情放进来（Vue Teleport：DOM 在抽屉里，组件仍属于页面，事件和状态照常）。
    打开详情时自动展开抽屉并切到“详情”；关闭后恢复抽屉原来的状态（原来收起就收起，原来在“AI 助手”就回到那里）。
    正在提交时不能关闭。停靠时详情区是非模态对话框（`role="dialog"`，Escape 关闭）；侧滑面板里由 Sheet 承担对话框。
- **Task Drawer**（规范 §8.4）：任务页的新建 / 编辑从弹窗改为放进抽屉的“详情”页。表单、幂等键、版本冲突、结果未知等行为不变。

## 5. 顶栏“创建”菜单（T-0067，规范 §8.1）

- 顶栏的“创建”按钮（替换原来的“创建 · 即将开放”占位）打开 shadcn-vue 下拉菜单（reka-ui DropdownMenu：键盘上下选择、Enter 选中、Escape 关闭）。
- 条目按角色列出，和侧栏导航、路由守卫的可见范围一致：
  - member 及以上：新建任务、新建日程、收藏文档；
  - owner / admin 另有：新建群聊、添加成员；owner 另有：登记 OpenClaw 实例；
  - viewer 不能新建任何内容，不显示这个按钮。真正的权限仍由服务端校验。
- **不另写一套新建表单**：选中后跳到对应页面并带上 `?create=1`，页面用 `useCreateIntent` 打开它已有的新建表单
  （任务进 Task Drawer，日程、收藏、建群、登记实例是原来的弹窗，添加成员是把焦点移到设置页的表单）。
  幂等键、结果未知、版本冲突、错误提示都沿用页面原有的实现，只有一份。
- 参数用过就用 `router.replace` 从地址里去掉：刷新、后退不会再弹出表单；在当前页再次选择同一项也能打开。
  只去掉参数的那次跳转不滚动页面（`scrollBehavior`）。
- 页面暂时不能新建时只去掉参数，原因由页面自己显示（例如没有配置知识来源、IM 未部署、有未结束的任务操作）；
  还没有知识来源时，管理员选“收藏文档”会先打开知识来源配置。
- 焦点：reka-ui 关闭菜单后会用 `setTimeout` 把焦点还给触发按钮，这会抢走表单里刚聚焦的输入框。所以选中条目时由菜单接管：
  阻止默认的焦点归还，下一轮先把焦点放回“创建”按钮再跳转；表单打开时焦点进入第一个输入框，关闭后回到“创建”按钮。
  不选条目直接关闭菜单时保持 reka-ui 的默认行为。

## 6. 没有做的

- 其他页面的详情（日历日程、收藏等）仍用原来的弹窗，可以按需改用 `DrawerDetail`。
- 现有按钮、表单没有整体换成 shadcn-vue 组件（只在新写的抽屉里使用），避免一次性改动全部页面。
