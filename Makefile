# My Workspace 根目录命令（docs/DEVELOPMENT_SPEC.md 第 11.3 节）。
# 前端部分调用 apps/web 的 npm scripts；apps/web 尚不存在时自动跳过（见 docs/decisions/2026-10-29-sprint-0-contracts.md）。

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := services/api
WEB_DIR := apps/web
UV ?= uv
NPM ?= npm
# 运行仓库脚本的 Python：优先 3.12（一键部署脚本会安装，与 CI、api 镜像一致；T-0074），没有时用 python3
PYTHON ?= $(shell command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || echo python3)
COMPOSE ?= docker compose
HAS_WEB := $(wildcard $(WEB_DIR)/package.json)
# 独立部署的 OpenIM：项目名与 core 分开，并告诉它 core 的项目名以加入 core 的网络
# （T-0082 移除了 Vikunja，项目与任务由工作台自己管理）
WORKSPACE_PROJECT := $(or $(COMPOSE_PROJECT_NAME),my-workspace)
# 独立部署的 OpenIM（infra/openim/compose.yaml），加入 core 的 im 网络；部署了 OpenClaw 接入入口（agent-im 网络存在）时
# 再叠加 compose.agents.yaml。用 shell 的 $$(...) 在执行时判断，不用 $(shell)：make 会在执行前展开整个命令
OPENIM_COMPOSE = WORKSPACE_PROJECT=$(WORKSPACE_PROJECT) $(COMPOSE) -p $(WORKSPACE_PROJECT)-openim \
	-f infra/openim/compose.yaml \
	$$(docker network inspect $(WORKSPACE_PROJECT)_agent-im >/dev/null 2>&1 && echo -f infra/openim/compose.agents.yaml) \
	--env-file .env
# 新闻早报（T-0152）：独立部署的 TrendRadar（infra/trendradar/compose.yaml），加入 core 的 news 网络与 trendradar-config 卷
TRENDRADAR_COMPOSE = WORKSPACE_PROJECT=$(WORKSPACE_PROJECT) $(COMPOSE) -p $(WORKSPACE_PROJECT)-trendradar \
	-f infra/trendradar/compose.yaml --env-file .env
# 读取 .env 里的 HTTP_PORT（没有时用 8080），供 make smoke 使用
HTTP_PORT := $(or $(shell sed -n 's/^HTTP_PORT=//p' .env 2>/dev/null),8080)

define skip_web
	@echo "跳过前端：$(WEB_DIR)/package.json 尚不存在"
endef

.PHONY: module-new help env setup dev dev-api dev-web up down ps logs migrate migrate-local smoke openapi setup-token \
	lint lint-api lint-web format test test-api test-web build-web backup restore-verify \
	smoke-full perf scan \
	agent-ca agents-up pull openim-pull \
	openim-build openim-up openim-down openim-logs \
	trendradar-pull trendradar-prompts trendradar-up trendradar-down trendradar-logs

help: ## 列出可用命令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

env: ## 从 .env.example 生成 .env，并随机生成密码与密钥（已存在则不改）
	@$(PYTHON) scripts/init_env.py

setup: env ## 准备本地开发环境：生成 .env 并安装前后端依赖
	cd $(API_DIR) && $(UV) sync
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) ci
else
	$(skip_web)
endif

setup-token: ## 显示首次安装所需的安装凭据（来自 .env 的 SETUP_TOKEN）
	@sed -n 's/^SETUP_TOKEN=//p' .env

dev: ## 本地开发：同时启动 API（热重载，:8000）和前端（:5173）
	@trap 'kill 0' INT TERM EXIT; \
	(cd $(API_DIR) && $(UV) run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) & \
	if [ -n "$(HAS_WEB)" ]; then $(NPM) --prefix $(WEB_DIR) run dev; else echo "前端尚未创建，只启动 API"; wait; fi

dev-api: ## 只启动 API（热重载）
	cd $(API_DIR) && $(UV) run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-web: ## 只启动前端开发服务器
	$(NPM) --prefix $(WEB_DIR) run dev

pull: env ## 拉取 core 用到的镜像（不含本地构建的 api、web；deploy.sh 失败时重试，T-0092）
	$(COMPOSE) pull --ignore-buildable

up: env ## 构建并启动 core 服务，等待全部健康检查通过
	$(COMPOSE) up -d --build --wait --wait-timeout 300
	@echo "已启动：http://localhost:$(HTTP_PORT)  首次启动后请运行 make migrate；首次安装凭据：make setup-token"

down: ## 停止服务；保留数据卷，不会删除数据
	$(COMPOSE) down

ps: ## 查看服务状态
	$(COMPOSE) ps

logs: ## 跟踪全部服务日志（Ctrl+C 退出）
	$(COMPOSE) logs -f --tail=100

migrate: ## 在运行中的 api 容器里把数据库迁移到最新版本（需先 make up）
	$(COMPOSE) exec -T api alembic upgrade head

migrate-local: ## 本机直接运行迁移（使用环境变量 DATABASE_URL）
	cd $(API_DIR) && $(UV) run alembic upgrade head

agent-ca: ## 导出 OpenClaw 外网入口信任的内部 CA 证书（不含私钥）到 infra/caddy/agent-ca/；需要先 make up
	docker compose exec -T api python -m app.commands.agent_ca export > infra/caddy/agent-ca/agent-ca.pem
	@echo "已导出 infra/caddy/agent-ca/agent-ca.pem；接入入口见 docs/deployment.md 第 16 节"

agents-up: agent-ca ## 启动 OpenClaw 接入入口（agent-gateway，--profile agents）；证书与变量见 docs/deployment.md 第 16 节
	docker compose --profile agents up -d agent-gateway
	@# 先部署了 OpenIM：让 openim-server 加入接入入口刚创建的 agent-im 网络，实例才能连上消息网关
	@if [ -n "$$(docker ps -q --filter label=com.docker.compose.project=$(WORKSPACE_PROJECT)-openim)" ]; then \
		echo "OpenIM 已在运行：让 openim-server 加入 agent-im 网络"; $(OPENIM_COMPOSE) up -d --wait --wait-timeout 600; fi

openim-build: ## 构建打过补丁的 OpenIM 镜像（infra/openim/server/build.sh；约 8 GB 临时磁盘）
	infra/openim/server/build.sh

openim-pull: env ## 拉取 OpenIM 依赖的镜像（openim-server 是 make openim-build 在本机构建的，不拉取）
	$(OPENIM_COMPOSE) pull $$($(OPENIM_COMPOSE) config --services | grep -vx openim-server)

openim-up: env ## 启动独立部署的 OpenIM（IM 后端）；需要先 make up 与 make openim-build（一键脚本：deploy.sh connect）
	@docker network inspect $(WORKSPACE_PROJECT)_im >/dev/null 2>&1 || \
		{ echo "缺少 core 的 im 网络：请先 make up" >&2; exit 1; }
	@docker network inspect $(WORKSPACE_PROJECT)_agent-im >/dev/null 2>&1 || \
		echo "没有部署 OpenClaw 接入入口：OpenIM 先只给消息页用；以后 make agents-up 时会自动让实例能连上"
	$(OPENIM_COMPOSE) up -d --wait --wait-timeout 600
	@grep -q '^OPENIM_API_URL=.' .env || { \
		echo "OpenIM 已启动。第一次部署时在 .env 里设置 OPENIM_API_URL=http://openim-server:10002 与"; \
		echo "OPENIM_OBJECT_URL=http://garage:3900，再运行 make up 让 api、worker 生效；步骤见 docs/deployment.md 第 17 节"; }

openim-down: ## 停止 OpenIM；保留数据卷
	$(OPENIM_COMPOSE) down

openim-logs: ## 跟踪 OpenIM 日志（Ctrl+C 退出）
	$(OPENIM_COMPOSE) logs -f --tail=100

trendradar-pull: env ## 拉取新闻早报 TrendRadar 的镜像（锁定 digest）
	$(TRENDRADAR_COMPOSE) pull

trendradar-prompts: ## 下载 TrendRadar 的 AI 提示词（锁定的上游提交，校验 sha256）写进配置卷；需要先 make up
	scripts/trendradar-prompts.sh $(WORKSPACE_PROJECT)_trendradar-config

trendradar-up: env ## 启动新闻早报（TrendRadar 与它的 MCP）；需要先 make up（一键脚本：deploy.sh 的可选组件）
	@docker network inspect $(WORKSPACE_PROJECT)_news >/dev/null 2>&1 || \
		{ echo "缺少 core 的 news 网络：请先 make up" >&2; exit 1; }
	scripts/trendradar-prompts.sh $(WORKSPACE_PROJECT)_trendradar-config
	@# 配置文件由工作台生成；还没有时先让 api 写一份“不抓取、不推送”的配置，TrendRadar 才能启动
	@$(COMPOSE) exec -T api test -f /var/lib/workspace/trendradar/config.yaml 2>/dev/null || \
		$(COMPOSE) exec -T api python -m app.commands.news_config >/dev/null
	$(TRENDRADAR_COMPOSE) up -d --wait --wait-timeout 300
	@grep -q '^TRENDRADAR_MCP_URL=.' .env || { \
		echo "TrendRadar 已启动。第一次部署时在 .env 里设置 TRENDRADAR_MCP_URL=http://trendradar-mcp:3333/mcp，"; \
		echo "再运行 make up 让 api、worker 生效；然后在网页“设置 → 新闻早报”里启用。步骤见 docs/deployment.md"; }

trendradar-down: ## 停止新闻早报；保留抓到的数据
	$(TRENDRADAR_COMPOSE) down

trendradar-logs: ## 跟踪 TrendRadar 日志（Ctrl+C 退出）
	$(TRENDRADAR_COMPOSE) logs -f --tail=100

smoke: ## 经 Caddy 检查健康、就绪、前端页面与安全响应头
	scripts/smoke.sh http://127.0.0.1:$(HTTP_PORT)

smoke-full: smoke ## 完整端到端冒烟：全部读接口与页面、核心服务、SSE、写入往返、登出（需要 tests/e2e 的依赖与 Chromium）
	node tests/e2e/full_smoke.cjs http://127.0.0.1:$(HTTP_PORT)

perf: ## 性能基准：基础 API 与首页的 P50/P95/P99，对比规范第 12 节的目标（tests/perf）
	$(PYTHON) tests/perf/benchmark.py --base-url http://127.0.0.1:$(HTTP_PORT)

scan: ## 安全扫描：依赖漏洞、密钥泄露、容器镜像（scripts/security-scan.sh；镜像扫描要先 make up）
	scripts/security-scan.sh

module-new: ## 新建插拔式模块骨架：make module-new key=finance name=记账 [description=一句话说明]（docs/modules/README.md）
	@test -n "$(key)" -a -n "$(name)" || (echo "用法：make module-new key=finance name=记账" && exit 1)
	python3 scripts/new_module.py --key "$(key)" --name "$(name)" --description "$(description)"

openapi: ## 由后端代码生成 services/api/openapi.json（前端据此生成类型）
	cd $(API_DIR) && $(UV) run python -m scripts.export_openapi

lint: lint-api lint-web ## 代码质量检查（前后端）

lint-api:
	cd $(API_DIR) && $(UV) run ruff check . && $(UV) run ruff format --check . && $(UV) run mypy app migrations tests scripts

lint-web:
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) run lint
	$(NPM) --prefix $(WEB_DIR) run typecheck
else
	$(skip_web)
endif

format: ## 自动格式化后端代码
	cd $(API_DIR) && $(UV) run ruff format . && $(UV) run ruff check --fix .

test: test-api test-web ## 运行测试（前后端）

test-api: ## 后端测试；设置 TEST_DATABASE_URL / TEST_REDIS_URL 时同时跑集成测试
	cd $(API_DIR) && $(UV) run pytest

test-web:
ifneq ($(HAS_WEB),)
	$(NPM) --prefix $(WEB_DIR) run test
else
	$(skip_web)
endif

build-web: ## 构建前端生产包
	$(NPM) --prefix $(WEB_DIR) run build

backup: env ## 备份数据库到加密的 restic 仓库，并按 7/30/90 日清理（见 docs/deployment.md 第 14 节）
	scripts/backup.sh

restore-verify: ## 在隔离的临时环境恢复最新备份并做冒烟检查，结束后删除临时环境
	scripts/restore-verify.sh
