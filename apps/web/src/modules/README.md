# 前端插拔式模块

一个功能模块 = `src/modules/<key>/` 一个文件夹 + `registry.ts` 里登记一行。侧栏、路由、首页小组件、实时事件
都由宿主从注册表读，不要再去改 `app/navigation.ts`、`app/router.ts`、`widgets/registry.ts`、`useWorkspaceEvents.ts`。
完整的分步说明（后端清单、迁移、工具、检查命令）见 [`docs/modules/README.md`](../../../../docs/modules/README.md)；
参考实现是新闻早报 `news/`。

## 文件夹里有什么

```
src/modules/<key>/
  index.ts        必需：export default defineModule({...})
  XxxPage.vue     页面（路由按需加载）
  XxxCard.vue     首页小组件（可选，按需加载）
  api.ts、组件、样式……
```

测试放 `tests/<key>*.test.ts`。

## 登记

在 `registry.ts` 顶部加 `import <key> from './<key>'`（一行写完），再在 `modules` 列表的
`// module-new:` 标记上面加一行。`make module-new key=... name=...` 会自动生成骨架并登记。

## defineModule 的字段（类型见 `types.ts`）

- `key`：与后端清单的 key 相同。
- `nav`：侧栏条目。`section` 用现有分组（`工作空间`、`连接与工具`、`管理`）或 `应用`（新模块默认放这里）；
  `after` 写排在哪一项后面（不写排在分组最后）；`minRole: 'admin' | 'owner'` 限制谁能看到和打开
  （路由守卫同样检查，后端还要自己做角色校验）；`icon` 用 `@lucide/vue` 的图标。
- `routes`：vue-router 路由，组件**必须**写成 `() => import('./XxxPage.vue')`，模块不启用就不下载它的代码。
  与 `nav` 同一地址的路由自动带上侧栏项的标题、说明和角色要求。
- `widgets`：用 `defineWidget({...})` 声明首页小组件：类型（必须已在后端组件联合类型里）、标题、默认大小、
  zod 配置（每个字段都要有默认值）、`component: () => import('./XxxCard.vue')`、`refreshPolicy: 'manual'`、
  “添加组件”里的填写说明 `help`。组件收到的 props 是 `value`（含 `config`），配置类型从自己的 zod 模式推出，
  不要导入 `widgets/registry.ts` 的类型。
- `events`：实时事件主题 → 要刷新的查询键前缀，例如 `{ news: ['news'] }`。主题必须已在后端
  `InvalidateEvent.topics` 里；查询键按惯例写成 `['<前缀>', userId, workspaceId, ...]`。
- `serverPanels`：服务器页面上的区块。`agentServer` 显示在智能体服务器的详情里，`runnerHost` 显示在容器服务器
  “服务器设置”的每台服务器下面；宿主用 `ModuleServerPanels.vue` 渲染已启用模块的区块，服务器页面不导入模块的代码。
  区块组件收到 props `serverId`、`serverName`，写成 `defineAsyncComponent(() => import('./XxxPanel.vue'))` 按需加载；
  外层用所在页面的面板样式。参考 `llm_bridge/index.ts` 的“MCP 管理器”。

## 规则

- 只从公共部分导入：`src/components`、`src/services`、`src/stores`、`src/composables`、`src/lib`，
  以及通用的小工具（`features/time.ts`、`features/kb/markdown.ts` 的安全渲染、`widgets/registry.ts` 的 `safeUrl`）；
  其余只导入自己文件夹里的东西，不导入别的模块。宿主也不导入模块内部，只经 `registry.ts`。
- 模块停用时：侧栏和搜索里的入口隐藏，打开它的地址显示“功能未启用”，首页小组件显示“功能已停用”——这些宿主自动处理。
  接口返回 `MODULE_DISABLED` 时用 `isModuleDisabled(error)`（`services/api.ts`）判断，显示“功能已停用”，不要当成普通错误。
- 界面文字用简短的中文，样式优先用 `style.css` 里已有的类；手机宽度（390px）下不能横向滚动。
- 需要用户设置的东西做成网页（设置页放在 `管理` 分组，`minRole: 'admin'`），部署级的配置才写进部署指南。
