# My Workspace 根目录命令（docs/DEVELOPMENT_SPEC.md 第 11.3 节）。
# 前端部分调用 apps/web 的 npm scripts；apps/web 尚不存在时自动跳过（见 docs/decisions/2026-10-29-sprint-0-contracts.md）。

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := services/api
WEB_DIR := apps/web
UV ?= uv
NPM ?= npm
COMPOSE ?= docker compose
HAS_WEB := $(wildcard $(WEB_DIR)/package.json)
# 独立部署的 Vikunja（infra/vikunja/compose.yaml）：项目名与 core 分开，并告诉它 core 的项目名以加入 integrations 网络
WORKSPACE_PROJECT := $(or $(COMPOSE_PROJECT_NAME),my-workspace)
VIKUNJA_COMPOSE = WORKSPACE_PROJECT=$(WORKSPACE_PROJECT) $(COMPOSE) -p $(WORKSPACE_PROJECT)-vikunja \
	-f infra/vikunja/compose.yaml --env-file .env
VIKUNJA_URL := $(or $(shell sed -n 's/^VIKUNJA_PUBLIC_URL=//p' .env 2>/dev/null),http://localhost:3456/)
# 可选的 n8n（infra/n8n/compose.yaml），同样加入 core 的 integrations 网络
N8N_COMPOSE = WORKSPACE_PROJECT=$(WORKSPACE_PROJECT) $(COMPOSE) -p $(WORKSPACE_PROJECT)-n8n \
	-f infra/n8n/compose.yaml --env-file .env
N8N_URL := $(or $(shell sed -n 's/^N8N_PUBLIC_URL=//p' .env 2>/dev/null),http://localhost:5678/)
# 读取 .env 里的 HTTP_PORT（没有时用 8080），供 make smoke 使用
HTTP_PORT := $(or $(shell sed -n 's/^HTTP_PORT=//p' .env 2>/dev/null),8080)

define skip_web
	@echo "跳过前端：$(WEB_DIR)/package.json 尚不存在"
endef

.PHONY: help env setup dev dev-api dev-web up down ps logs migrate migrate-local smoke openapi setup-token \
	lint lint-api lint-web format test test-api test-web build-web backup restore-verify \
	vikunja-up vikunja-down vikunja-user vikunja-logs n8n-up n8n-down n8n-logs

help: ## 列出可用命令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

env: ## 从 .env.example 生成 .env，并随机生成密码与密钥（已存在则不改）
	@python3 scripts/init_env.py

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

vikunja-up: env ## 启动独立部署的 Vikunja（任务主源）；需要先 make up
	$(VIKUNJA_COMPOSE) up -d --wait --wait-timeout 180
	@echo "Vikunja 原生界面：$(VIKUNJA_URL)  首次使用请运行 make vikunja-user 创建用户（自助注册已关闭）"

vikunja-user: ## 创建 Vikunja 用户（依次输入用户名、邮箱和密码）
	@read -r -p "用户名：" name; read -r -p "邮箱：" email; \
	$(VIKUNJA_COMPOSE) exec vikunja /app/vikunja/vikunja user create -u "$$name" -e "$$email"

vikunja-down: ## 停止 Vikunja；保留数据卷
	$(VIKUNJA_COMPOSE) down

vikunja-logs: ## 跟踪 Vikunja 日志（Ctrl+C 退出）
	$(VIKUNJA_COMPOSE) logs -f --tail=100

n8n-up: env ## 启动独立部署的 n8n（自动化，可选）；需要先 make up
	$(N8N_COMPOSE) up -d --wait --wait-timeout 300
	@echo "n8n 编辑器：$(N8N_URL)  首次打开时创建 owner 账号；工作台的接入步骤见 docs/deployment.md 第 12.4 节"

n8n-down: ## 停止 n8n；保留数据卷
	$(N8N_COMPOSE) down

n8n-logs: ## 跟踪 n8n 日志（Ctrl+C 退出）
	$(N8N_COMPOSE) logs -f --tail=100

smoke: ## 经 Caddy 检查健康、就绪、前端页面与安全响应头
	scripts/smoke.sh http://127.0.0.1:$(HTTP_PORT)

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
