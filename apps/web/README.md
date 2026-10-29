# My Workspace Web

Sprint 0 / T-0004 前端骨架：Vue 3、TypeScript、Vite、Vue Router、Pinia、TanStack Vue Query、Tailwind CSS。

在本目录使用 Node.js 22（至少 22.12）和 npm：

```sh
npm ci
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
```

开发服务绑定 127.0.0.1:5173；构建产物位于 `dist/`。根目录 `make dev-web`、`make lint-web`、`make test-web`、`make build-web` 可调用同一组命令。

`/api`、`/healthz`、`/readyz` 在开发模式代理到 `http://localhost:8000`；页面自身不主动请求这些接口。
生产服务须为 HTML5 history 路由配置 `index.html` 回退，API 路径须先转发后端。容器和 Caddy 配置由 T-0006 实现。

当前所有业务页面均为明确标注的占位页，不提供登录、任务、搜索或外部服务连接。主题优先读取本地偏好，再读取系统偏好；仅将主题写入 localStorage，存储不可用时可继续使用。小屏通过顶栏按钮展开导航，跳转后收起。

验证说明见 `VALIDATION.md`，精确依赖版本和技术选择见 `../../docs/decisions/2026-10-30-frontend-stack.md`。
