# Vikunja / n8n 的对外站点（可选）

`scripts/deploy.sh connect` 在设置了 `VIKUNJA_DOMAIN` / `N8N_DOMAIN` 时在这里生成 `vikunja.caddy` / `n8n.caddy`，
HTTPS 模式的 Caddy（`compose.https.yaml`）把本目录挂载为 `/etc/caddy/apps`，由 `infra/caddy/https.Caddyfile` 导入。
生成的文件含本机的域名，不提交（`.gitignore`）；目录为空时不对外开放任何子域名。说明见 `docs/deploy-guide.md`。
