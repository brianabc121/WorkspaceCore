# My Workspace Web

Sprint 1 / T-0012：Vue 3、TypeScript、Vite、Vue Router、Pinia、TanStack Vue Query、Tailwind CSS。

在本目录使用 Node.js 22（`.nvmrc` 为 22.23.4）和 npm：

```sh
npm ci
npm run generate:api
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

开发服务绑定 127.0.0.1:5173；构建产物位于 `dist/`。根目录 `make dev-web`、`make lint-web`、`make test-web`、`make build-web` 可调用同一组命令。

`/api`、`/healthz`、`/readyz` 在开发模式代理到 `http://localhost:8000`。浏览器访问 `http://localhost:5173` 时，后端的 `CORS_ALLOWED_ORIGINS` 必须包含这个来源；详见认证契约第 4 节。生产服务须为 HTML5 history 路由配置 `index.html` 回退，API 路径须先转发后端。容器和 Caddy 配置由 T-0006 实现。

启动先检查安装状态，再获取当前用户。首次安装创建管理员与默认工作空间；部署管理员提供一次性安装凭据，不提供默认密码。安装完成后使用邮箱和密码登录。

- `/settings`：个人显示名与工作空间名称；member / viewer 只读工作空间名称。
- `/settings/security`：修改密码、会话列表、确认撤销自己的会话；撤销当前会话后回到登录。
- `/settings/audit`：owner / admin 的工作空间审计日志，使用服务端不透明游标分页。
- 未登录访问受保护路由返回登录；连接失败显示可重试状态。401 清空会话与查询缓存；403 / 404 隐藏已经失去访问权限的列表。工作空间切换丢弃旧审计查询，迟到的旧会话响应不会恢复用户或清除新会话。

客户端只请求同源 `/api/v1/...` 路径，使用浏览器 Cookie，不读取会话 Token。每次写请求重新读取 `ws_csrf`（包括改密码换发后的 Cookie）；工作空间资源带 `X-Workspace-Id`。请求超时 15 秒，不自动重试；写入连接中断会提示结果未知，先刷新核实再重试。429 使用 `Retry-After` 提示，统一错误保留 request ID 以供排查，但不直接渲染网关响应正文。

`src/services/schema.d.ts` 由 `../../services/api/openapi.json` 生成，不手改。契约变化后运行 `npm run generate:api` 并提交；`npm run check:api` 与 CI 检查生成结果是否一致。`openapi-fetch` 使用生成的 paths 校验请求与响应类型；Vee Validate + Zod 校验表单，API 数据类型引用生成的 schemas。

新增依赖固定为 openapi-typescript 7.13.0、openapi-fetch 0.17.0、vee-validate / @vee-validate/zod 4.15.1、Zod 3.25.76（对应适配器的 Zod 3 版本）。来源：[OpenAPI TypeScript](https://openapi-ts.dev/introduction)、[OpenAPI Fetch](https://openapi-ts.dev/openapi-fetch/)、[Vee Validate Zod](https://vee-validate.logaretm.com/v4/integrations/zod-schema-validation/)。锁文件固定传递依赖。

其他业务页面仍明确标注为规划中，不提供任务、搜索或外部服务连接。主题优先读取本地偏好，再读取系统偏好；仅将主题写入 localStorage，存储不可用时可继续使用。小屏通过顶栏按钮展开导航，跳转后收起。

验证说明见 `VALIDATION.md`；基础栈选择见 `../../docs/decisions/2026-10-30-frontend-stack.md`，接口约定见 `../../docs/decisions/2026-10-30-sprint-1-auth-contract.md`。
