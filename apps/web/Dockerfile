# 前端镜像：npm ci + npm run build → 非 root 的 nginx 提供静态文件（SPA history 回退）。
# 本文件归 claude 维护（docs/decisions/2026-10-29-sprint-0-contracts.md 第 1 节）；应用代码归 codex。
# 安全响应头由前置的 Caddy 统一设置（infra/caddy/Caddyfile）。

FROM node:22.23.4-alpine3.24 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# 用 slim 变体：只有 nginx 与 Alpine 基础包，没有 curl、freetype、brotli 等用不到的库。
# 完整变体在 T-0044 的 trivy 扫描里有 3 个已有修复的高危 / 严重漏洞（brotli-libs、curl / libcurl），slim 变体为 0。
# 健康检查用 busybox 的 wget（compose.yaml）。digest 见 docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md 第 8 节。
FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24-slim@sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 8080;
    server_name _;
    server_tokens off;
    root /usr/share/nginx/html;

    # 容器健康检查用；Caddy 不会把外部请求转到这个路径
    location = /nginx-health {
        access_log off;
        default_type text/plain;
        return 200 "ok\n";
    }

    # Vite 产物文件名带内容哈希，可以长期缓存
    location /assets/ {
        try_files $uri =404;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # 其余路径回退到 index.html，交给 Vue Router（history 模式）
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache";
    }
}
EOF
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
